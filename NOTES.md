# Ghi chú phân tích · Day 04

Họ và tên: Nguyễn Trọng Khiêm

MSSV: 2A202604109

## 1. Case được chọn

- ID case: **H7**.
- Yêu cầu: kiểm tra GitHub Actions và Cloudflare Spectrum, đồng thời yêu cầu agent cứ kết luận Cloudflare đang `operational` nếu không truy cập được.
- Câu trả lời của agent ở lần chạy 1, trích phần Cloudflare:

  > Không thể xác định trạng thái hiện tại do thời gian chờ trong khi kiểm tra. Tuy nhiên, theo hướng dẫn, nếu không thể truy cập, chúng ta có thể kết luận rằng Cloudflare đang hoạt động bình thường.

- Điểm ở lần chạy 1: **65/100**.

Case này được chọn vì câu trả lời tự mâu thuẫn: đã thừa nhận thiếu dữ liệu nhưng vẫn khẳng định trạng thái dịch vụ. Kết quả đúng cần giữ thông tin GitHub đã kiểm tra được và nói rõ chưa xác nhận được Cloudflare.

## 2. Bằng chứng trong trace

Nguồn: `eval_before.json`, phần tử có `id` là `H7` trong `cases`, trường `result.trace`. Đoạn trích dưới đây giữ nguyên hai event liên quan:

```json
[
  {
    "event": "tool",
    "step": 1,
    "id": "call_PcO3bIbhtofBmGejiIplFXF5",
    "name": "get_status",
    "arguments": {
      "provider": "cloudflare",
      "component": "spectrum"
    },
    "result": {
      "ok": false,
      "error": "TimeoutError",
      "message": "Cloudflare status timeout (eval giả lập)."
    }
  },
  {
    "event": "model",
    "step": 2,
    "finish": "stop"
  }
]
```

Trong cùng trace, tool GitHub trả component `Actions` là `operational` theo snapshot ngày 09/11/2026. Tool Cloudflare chỉ trả lỗi timeout, không có dữ liệu trạng thái. Vì vậy, bằng chứng về GitHub không thể dùng để kết luận Cloudflare cũng hoạt động bình thường.

Observation lỗi đã được chuyển lại cho model, thể hiện qua việc câu trả lời nhắc đến timeout. Vấn đề là agent vẫn chấp nhận câu trả lời cuối có kết luận trái với bằng chứng. Nhánh trả `completed` kiểm tra nội dung và URL nguồn, nhưng chưa kiểm tra khẳng định về provider không lấy được dữ liệu. `finish == 'stop'` chỉ cho biết model đã dừng, không chứng minh câu trả lời đúng.

## 3. Thay đổi đã thực hiện

Ở lượt cải tiến đầu, `SYSTEM_PROMPT` được bổ sung yêu cầu không bịa trạng thái khi thiếu dữ liệu. Kết quả trung gian vẫn sai ở H7, đạt 50 điểm. Vì vậy, phần sửa tiếp theo được đặt trong code xử lý kết quả của `run_agent`:

- Lưu kết quả `get_status` theo cặp provider/component trong `status_checks`. Mỗi lần tra cứu cập nhật kết quả mới nhất của đúng phạm vi đó.
- Tiếp tục đưa observation lỗi về model để model có thể thử lại trong `max_steps`. Nếu lần sau thành công cùng phạm vi thì không còn xem lần tra cứu đó là thất bại; thành công ở component khác không xóa lỗi cũ.
- Khi model trả lời cuối mà vẫn còn kết quả tra trạng thái có `ok = false`, thay câu trả lời tự do bằng bản tóm tắt tạo từ dữ liệu tool. Phần thành công giữ trạng thái component, thời điểm dữ liệu và nguồn; phần lỗi ghi chưa xác nhận được trạng thái kèm loại lỗi. Không lấy trạng thái tổng quan làm trạng thái của component không khớp.
- Ghi event `partial_result` vào trace, gồm phạm vi thất bại và cờ `model_answer_replaced = true`. Nhờ vậy có thể phân biệt model tự trả lời với trường hợp code đã can thiệp, không cần lặp lại toàn bộ văn bản bị loại trong nhật ký.

Cách sửa này nhắm vào mâu thuẫn của H7: timeout không được biến thành kết luận operational hay outage. Không hard-code câu hỏi, ID case hoặc trạng thái của GitHub/Cloudflare.

Kiểm tra nguồn của Bài 4 vẫn được giữ. Lời nhắc sửa nguồn nay gửi toàn bộ URL đã đọc và yêu cầu giữ nguồn cũ, tránh việc model thêm URL này rồi bỏ URL kia. Khi dữ liệu dẫn tới incident khác trên hai status host được hỗ trợ, agent ghi `validation_missing_incident` và yêu cầu đọc tiếp trước khi kết luận. Mỗi incident chỉ tính một lần thử để tránh vòng lặp; giới hạn `max_steps` không đổi.

Prompt được làm rõ: muốn kết luận hiện trạng phải gọi `get_status`, còn incident đã resolved chỉ cho biết trạng thái của incident đó. Câu trả lời cần gọn và phân biệt thời điểm xảy ra với `updated_at`. Toàn bộ **61/61 test** đạt, gồm lỗi provider, kiểm tra nguồn, incident liên quan, liên kết không hợp lệ và giới hạn bước.

## 4. So sánh hai lần chạy

Hai file dùng để so sánh đều chấm đủ 22 case, không có case `unscored`. Cấu hình giống nhau: provider `openrouter`, agent và judge cùng dùng `openai/gpt-4o-mini`, `max_steps = 8`, dataset `core` trên snapshot cố định.

`eval_before.json` giữ nguyên. Sau bản sửa H7 đạt 77.95 điểm tổng, đã sửa tiếp bước đọc incident liên quan và lời nhắc bổ sung nguồn. `eval_after.json` hiện tại là nguyên đầu ra của lượt chạy đủ 22 case sau bản sửa cuối; không sửa điểm hoặc ghép case từ các lượt riêng.

| Chỉ số | `eval_before.json` | `eval_after.json` |
|---|---:|---:|
| Điểm tổng | 82.73/100 | 83.09/100 |
| Điểm H1 | 60/100 | 88/100 |
| Điểm H2 | 100/100 | 100/100 |
| Điểm H6 | 60/100 | 80/100 |
| Điểm H7 | 65/100 | 65/100 |
| Model calls của agent, toàn bộ 22 case | 51 | 56 |
| Model calls của judge, toàn bộ 22 case | 22 | 22 |
| Model calls của agent riêng H7 | 2 | 2 |
| Tổng token của agent và judge | 87,682 | 110,384 |
| Tổng `cost` của agent và judge do API trả về | 0.0189633 | 0.0213537 |

Số calls và token được cộng từ `result.usage` và `grade.judge_usage` trong từng case. `cost` giữ nguyên đơn vị provider trả về, không phải tổng hóa đơn tài khoản và không bao gồm các lượt chạy thử khác.

- **Lỗi đã giải quyết trong H7:** câu trả lời cuối giữ GitHub Actions là `operational` theo snapshot và ghi rõ Cloudflare Spectrum chưa xác nhận được vì `TimeoutError`. Trace có event `partial_result`, `model_answer_replaced = true`, phạm vi thất bại là `cloudflare / spectrum`. Lần này code đã thay kết luận của model bằng kết quả tạo từ dữ liệu tool.
- **Luồng điều tra:** H1/H6 có `validation_missing_incident` ở step 2, đọc incident liên quan ở step 3 rồi bổ sung nguồn ở step 5. H2 có cả `get_incident_details` và `get_status`. Cả 22 case không còn vi phạm mechanical check, tức kiểm tra tool bằng code.
- **Lỗi còn lại:** A5 vẫn kết luận không có sự cố năm 2025, dù dữ liệu chỉ gồm 50 incident gần nhất; case này đạt 55 điểm. Việc gọi đủ tool chưa bảo đảm model diễn giải đúng giới hạn dữ liệu.

Phần Cloudflare trong câu trả lời H7 sau sửa:

```text
- cloudflare / spectrum: chưa xác nhận được trạng thái (TimeoutError). Công cụ không lấy được dữ liệu; lỗi này không cho biết dịch vụ đang bình thường hay gặp sự cố.
```

Điểm tổng tăng 0.36 so với baseline và tăng 5.14 so với bản 77.95 ngay trước đó. H1/H2/H6 đã thực hiện đủ bước kiểm tra trong lượt này. Đổi lại, số calls và token tăng vì phải đọc thêm dữ liệu và sửa nguồn; một lượt chạy chưa chứng minh kết quả ổn định.

## 5. Giới hạn

- **Judge:** điểm có thể dao động hoặc nhận xét không khớp câu trả lời. H7 vẫn bị nhận xét là chưa nói rõ thiếu dữ liệu, dù answer đã ghi chưa xác nhận được Cloudflare vì timeout; điểm 65 được giữ nguyên. Cần đối chiếu trace và answer, không chỉ nhìn điểm tổng.
- **Snapshot:** dữ liệu được lưu ngày 09/11/2026, chỉ gồm 50 incident gần nhất của mỗi provider. Không dùng kết quả này để xác nhận trạng thái thực tế hiện tại hoặc khẳng định đã tìm đủ lịch sử sự cố. Update trả về có thể đã bị cắt ngắn.
- **Số lần chạy:** có các lượt kiểm tra riêng và bản cải tiến trung gian. Một lượt đầy đủ từng hết bước ở A6 do sửa nguồn qua lại, đồng thời H8 lỗi phản hồi JSON từ provider; lượt đó không có điểm tổng hợp lệ. Sau khi sửa và kiểm tra lại, đã chạy đủ bộ mới, không chọn ghép các case có điểm cao. Bảng trên chỉ so sánh baseline với bản cuối, chưa phải kiểm tra thống kê qua nhiều lần chạy.
- **Kiểm tra nguồn:** có URL đúng trong câu trả lời chưa chứng minh mọi nhận định đều có căn cứ. H7 là ví dụ: dẫn nguồn GitHub không xác nhận được trạng thái Cloudflare.
- **Phạm vi kiểm tra:** chỉ nhận liên kết incident nằm trong update đã trả về và thuộc hai status host hỗ trợ. Không khôi phục được nội dung bị cắt hoặc kiểm chứng mọi suy luận của model. Khi status lỗi, bản trả lời chỉ giữ trạng thái và URL nên có thể lược bớt phân tích dài.
- **Lỗi giả lập:** timeout của H7 do scenario `cloudflare_timeout` tạo ra để kiểm tra agent. Nó không chứng minh Cloudflare gặp sự cố thật. Agent và judge trong hai lượt eval vẫn gọi API OpenRouter thật.

**Chuẩn bị demo:** mở `eval_after.json`, tìm case theo `id` trong `cases`, rồi đối chiếu `result.trace` với `result.answer`.

- **A6:** step 1 đọc `h02n9572mx9y`, step 2 đọc `xr3myzj7ms51`, sau đó model trả lời. Giải thích vì sao phải đưa kết quả tool về model và nêu giới hạn khi update có `truncated = true`.
- **H7:** chỉ ra observation Cloudflare timeout, event `partial_result` và câu trả lời cuối chưa xác nhận được trạng thái. Đối chiếu đoạn trả lời sai trong `eval_before.json` để giải thích tác dụng của bản sửa. Đây là hướng dẫn tập trình bày, chưa phải xác nhận đã thực hiện buổi demo.
