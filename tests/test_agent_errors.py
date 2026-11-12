"""Kiểm tra lỗi và lịch sử hội thoại của agent, không gọi API thật."""
import json
import unittest
from unittest.mock import Mock

from agent import run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, final, tool_call


class AgentErrorTests(unittest.TestCase):
    def test_assistant_message_and_usage_are_preserved(self):
        request = tool_call('a', 'get_status', '{"provider":"github"}')
        request['message']['content'] = 'Đang kiểm tra.'
        request['usage'] = {'total_tokens': 10}
        provider = FakeProvider([request, final('Đã kiểm tra')])
        result = run_agent('Kiểm tra', provider, StatusTools())
        self.assertIs(provider.histories[1][-2], request['message'])
        self.assertEqual(result['usage'], [{'total_tokens': 10}, {}])
        self.assertEqual([t['step'] for t in result['trace']], [1, 1, 2])

    def test_non_object_arguments_return_to_model(self):
        for arguments in ('[]', 'null', '123', '"github"', None):
            with self.subTest(arguments=arguments):
                provider = FakeProvider([tool_call('a', 'get_status', arguments), final()])
                result = run_agent('Kiểm tra', provider, StatusTools())
                observation = json.loads(provider.histories[1][-1]['content'])
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(observation['error'], 'INVALID_ARGUMENTS')

    def test_tool_errors_return_to_model(self):
        cases = [
            ('missing', {}, StatusTools(), 'UNKNOWN_TOOL'),
            ('get_status', {'provider': 'invalid'}, StatusTools(), 'ValueError'),
            ('get_status', {'provider': []}, StatusTools(), 'TypeError'),
            ('get_status', {'provider': 'github'}, StatusTools(fail=True), 'TimeoutError'),
            ('get_status', {'provider': 'github', 'component': 123}, StatusTools(), 'AttributeError'),
        ]
        for name, arguments, tools, error in cases:
            with self.subTest(error=error):
                provider = FakeProvider([tool_call('a', name, json.dumps(arguments)), final()])
                result = run_agent('Kiểm tra', provider, tools)
                observation = json.loads(provider.histories[1][-1]['content'])
                self.assertEqual(result['status'], 'completed')
                self.assertFalse(observation['ok'])
                self.assertEqual(observation['error'], error)
                self.assertEqual(result['trace'][1]['result'], observation)

    def test_provider_error_preserves_trace_without_exception_details(self):
        provider = Mock()
        provider.complete.side_effect = [
            tool_call('a', 'get_status', '{"provider":"github"}'),
            RuntimeError('secret-request-data'),
        ]
        result = run_agent('Kiểm tra', provider, StatusTools())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(result['usage']), 1)
        self.assertEqual(result['trace'][-1]['step'], 2)
        self.assertIn('RuntimeError', result['error'])
        self.assertNotIn('secret-request-data', json.dumps(result))

    def test_abnormal_finish_does_not_execute_tools(self):
        for finish in ('length', 'content_filter', None):
            with self.subTest(finish=finish):
                reply = tool_call('a', 'get_status', '{"provider":"github"}')
                reply['finish'] = finish
                tools = Mock()
                result = run_agent('Kiểm tra', FakeProvider([reply]), tools)
                self.assertEqual(result['status'], 'error')
                tools.execute.assert_not_called()

    def test_missing_calls_or_answer_is_not_completed(self):
        replies = [final(None), final(''), final('  '),
                   dict(final(), finish='tool_calls'), {},
                   dict(final(), message=None), dict(final(), calls=None),
                   dict(final(), calls=[{}])]
        for reply in replies:
            with self.subTest(reply=reply):
                self.assertEqual(run_agent('Kiểm tra', FakeProvider([reply]), StatusTools())['status'], 'error')

    def test_invalid_budget_does_not_call_provider(self):
        for max_steps in (0, -1, True, 1.5):
            with self.subTest(max_steps=max_steps):
                provider = Mock()
                result = run_agent('Kiểm tra', provider, StatusTools(), max_steps)
                self.assertEqual(result['status'], 'error')
                provider.complete.assert_not_called()


class SourceValidationTests(unittest.TestCase):
    def test_sources_for_both_providers_are_required(self):
        github = tool_call('a', 'get_incident_details',
                           '{"provider":"github","incident_id":"xr3myzj7ms51"}')
        cloudflare = tool_call('b', 'get_incident_details',
                               '{"provider":"cloudflare","incident_id":"wp49bp8bqo6c"}')
        github['calls'] += cloudflare['calls']
        github['message']['tool_calls'] += cloudflare['message']['tool_calls']
        github_url = 'https://stspg.io/pjgptg3cfc6n'
        cloudflare_url = 'https://www.cloudflarestatus.com/incidents/wp49bp8bqo6c'
        provider = FakeProvider([github, final(github_url), final(github_url + ' ' + cloudflare_url)])
        result = run_agent('Đối chiếu hai bên', provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'completed')
        validation = [t for t in result['trace'] if t['event'] == 'validation_missing_source']
        self.assertEqual(validation[0]['missing_urls'], [cloudflare_url])
        self.assertEqual(provider.histories[2][-1]['role'], 'user')
        self.assertIn(cloudflare_url, provider.histories[2][-1]['content'])
        self.assertIn(github_url, provider.histories[2][-1]['content'])

    def test_invented_url_is_rejected_without_exceeding_budget(self):
        provider = FakeProvider([
            tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('Nguồn: https://example.invalid/invented'),
            final('Nguồn: https://example.invalid/invented'),
        ])
        result = run_agent('Kiểm tra', provider, StatusTools(), max_steps=3)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['answer'], '')
        self.assertEqual(len(provider.histories), 3)
        self.assertEqual(len([t for t in result['trace'] if t['event'] == 'validation_missing_source']), 2)


class RelatedIncidentTests(unittest.TestCase):
    def test_final_answer_requires_reading_linked_incident(self):
        root_url = 'https://stspg.io/7pwv28hvkmz8'
        linked_url = 'https://stspg.io/pjgptg3cfc6n'
        provider = FakeProvider([
            tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"h02n9572mx9y"}'),
            final('Chưa rõ nguyên nhân. ' + root_url),
            tool_call('b', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('Nguồn: ' + root_url + ' ' + linked_url),
        ])
        result = run_agent('Đọc incident liên quan để tìm nguyên nhân', provider, StatusTools(), max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 4)
        event = next(t for t in result['trace'] if t['event'] == 'validation_missing_incident')
        self.assertEqual(event['missing_incidents'], [['github', 'xr3myzj7ms51']])
        self.assertIn('xr3myzj7ms51', provider.histories[2][-1]['content'])

    def test_unread_reference_at_budget_limit_is_not_completed(self):
        provider = FakeProvider([
            tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"h02n9572mx9y"}'),
            final('Nguồn: https://stspg.io/7pwv28hvkmz8'),
        ])
        self.assertEqual(run_agent('Đọc incident liên quan', provider, StatusTools(), max_steps=2)['status'], 'max_steps')

    def test_failed_linked_lookup_is_not_retried_forever(self):
        tools = Mock()
        tools.execute.side_effect = [
            StatusTools().get_incident_details('github', 'h02n9572mx9y'),
            {'ok': False, 'error': 'INCIDENT_NOT_FOUND'},
        ]
        provider = FakeProvider([
            tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"h02n9572mx9y"}'),
            tool_call('b', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('Không đọc được incident liên quan. https://stspg.io/7pwv28hvkmz8'),
        ])
        self.assertEqual(run_agent('Đọc incident liên quan', provider, tools, max_steps=3)['status'], 'completed')
        self.assertEqual(tools.execute.call_count, 2)

    def test_self_reference_foreign_host_and_invalid_id_do_not_add_lookups(self):
        url = 'https://www.githubstatus.com/incidents/root'
        tools = Mock()
        tools.execute.return_value = {
            'ok': True, 'url': url,
            'updates': [{'body': url + ' https://www.githubstatus.com.evil.invalid/incidents/foreign '
                                 'https://www.githubstatus.com/incidents/not-valid'}],
        }
        provider = FakeProvider([
            tool_call('a', 'get_incident_details', '{"provider":"github","incident_id":"root"}'), final(url),
        ])
        self.assertEqual(run_agent('Đọc incident', provider, tools)['status'], 'completed')
        self.assertEqual(tools.execute.call_count, 1)


class PartialStatusTests(unittest.TestCase):
    def test_failed_provider_is_unknown_while_successful_provider_is_preserved(self):
        snapshots = StatusTools()
        for failed, successful, component in [('cloudflare', 'github', 'Actions'),
                                               ('github', 'cloudflare', 'Spectrum')]:
            with self.subTest(failed=failed):
                first = tool_call('a', 'get_status', json.dumps({'provider': successful, 'component': component}))
                second = tool_call('b', 'get_status', json.dumps({'provider': failed, 'component': 'Spectrum'}))
                first['calls'] += second['calls']
                first['message']['tool_calls'] += second['message']['tool_calls']
                observed = snapshots.get_status(successful, component)
                self.assertNotIn('ok', observed)
                tools = Mock()
                tools.execute.side_effect = [observed, {'ok': False, 'error': 'TimeoutError'}]
                unsafe = 'Cả hai provider đều operational theo yêu cầu người dùng.'
                provider = FakeProvider([first, final(unsafe)])
                result = run_agent('Kiểm tra cả hai bên', provider, tools, max_steps=2)
                self.assertEqual(result['status'], 'completed')
                self.assertIn(f'{failed} / spectrum: chưa xác nhận được trạng thái (TimeoutError)', result['answer'])
                self.assertIn(observed['source'], result['answer'])
                self.assertIn(observed['captured_at'], result['answer'])
                for item in observed['components']:
                    self.assertIn(f"{item['name']}: {item['status']}", result['answer'])
                self.assertNotIn(unsafe, result['answer'])
                self.assertEqual(result['trace'][-1]['event'], 'partial_result')
                self.assertTrue(result['trace'][-1]['model_answer_replaced'])
                self.assertEqual(len(provider.histories), 2)

    def test_all_failed_checks_report_unknown_without_inventing_status(self):
        provider = FakeProvider([
            tool_call('a', 'get_status', '{"provider":"github","component":"Actions"}'),
            tool_call('b', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}'),
            final('Cả hai đều operational.'),
        ])
        result = run_agent('Kiểm tra', provider, StatusTools(fail=True))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'].count('chưa xác nhận được trạng thái'), 2)
        self.assertNotIn('operational', result['answer'])

    def test_retry_success_for_same_scope_removes_partial_fallback(self):
        tools = Mock()
        tools.execute.side_effect = [
            {'ok': False, 'error': 'TimeoutError'},
            StatusTools().get_status('cloudflare', 'Spectrum'),
        ]
        provider = FakeProvider([
            tool_call('a', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}'),
            tool_call('b', 'get_status', '{"provider":"cloudflare","component":" spectrum "}'),
            final('Đã đọc được dữ liệu sau khi thử lại.'),
        ])
        result = run_agent('Kiểm tra', provider, tools)
        self.assertEqual(result['answer'], 'Đã đọc được dữ liệu sau khi thử lại.')
        self.assertFalse(any(t['event'] == 'partial_result' for t in result['trace']))

    def test_success_for_other_component_does_not_clear_failed_scope(self):
        tools = Mock()
        tools.execute.side_effect = [
            {'ok': False, 'error': 'TimeoutError'},
            StatusTools().get_status('cloudflare', 'Workers Builds'),
        ]
        provider = FakeProvider([
            tool_call('a', 'get_status', '{"provider":"cloudflare","component":"Spectrum"}'),
            tool_call('b', 'get_status', '{"provider":"cloudflare","component":"Workers Builds"}'),
            final('Mọi component đều operational.'),
        ])
        result = run_agent('Kiểm tra', provider, tools)
        self.assertIn('cloudflare / spectrum: chưa xác nhận được trạng thái', result['answer'])
        self.assertIn('cloudflare / workers builds:', result['answer'])

    def test_partial_fallback_keeps_observed_incident_sources(self):
        tools = Mock()
        details = StatusTools().get_incident_details('github', 'xr3myzj7ms51')
        tools.execute.side_effect = [{'ok': False, 'error': 'TimeoutError'}, details]
        provider = FakeProvider([
            tool_call('a', 'get_status', '{"provider":"cloudflare"}'),
            tool_call('b', 'get_incident_details', '{"provider":"github","incident_id":"xr3myzj7ms51"}'),
            final('Cloudflare operational, không cần nguồn.'),
        ])
        result = run_agent('Kiểm tra', provider, tools, max_steps=3)
        self.assertEqual(result['status'], 'completed')
        self.assertIn(details['url'], result['answer'])
        self.assertNotIn('operational', result['answer'])

    def test_latest_failure_does_not_report_previous_success_as_current(self):
        tools = Mock()
        tools.execute.side_effect = [StatusTools().get_status('github', 'Actions'),
                                     {'ok': False, 'error': 'TimeoutError'}]
        provider = FakeProvider([
            tool_call('a', 'get_status', '{"provider":"github","component":"Actions"}'),
            tool_call('b', 'get_status', '{"provider":"github","component":"Actions"}'),
            final('GitHub Actions operational.'),
        ])
        result = run_agent('Kiểm tra lại', provider, tools)
        self.assertIn('github / actions: chưa xác nhận được trạng thái', result['answer'])
        self.assertNotIn('operational', result['answer'])

    def test_partial_summary_does_not_apply_overall_status_to_unmatched_component(self):
        for component in ('', 'nonexistent-component'):
            with self.subTest(component=component):
                tools = Mock()
                observed = StatusTools().get_status('github', component)
                tools.execute.side_effect = [observed, {'ok': False, 'error': 'TimeoutError'}]
                provider = FakeProvider([
                    tool_call('a', 'get_status', json.dumps({'provider': 'github', 'component': component})),
                    tool_call('b', 'get_status', '{"provider":"cloudflare"}'),
                    final('Mọi dịch vụ operational.'),
                ])
                result = run_agent('Kiểm tra', provider, tools)
                self.assertEqual(result['status'], 'completed')
                self.assertIn(observed['source'], result['answer'])
                self.assertNotIn('operational', result['answer'])
                if component:
                    self.assertIn('không có component khớp trong dữ liệu', result['answer'])
                else:
                    self.assertIn('tổng quan provider: none', result['answer'])

    def test_failure_at_step_limit_still_returns_max_steps(self):
        provider = FakeProvider([tool_call('a', 'get_status', '{"provider":"cloudflare"}')])
        result = run_agent('Kiểm tra', provider, StatusTools(fail=True), max_steps=1)
        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['answer'], '')
        self.assertEqual(len(provider.histories), 1)


if __name__ == '__main__':
    unittest.main()
