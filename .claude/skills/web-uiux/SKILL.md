---
name: web-uiux
description: Làm việc với GIAO DIỆN WEB của project (Flask chat UI trong webapp/app.py). Dùng khi người dùng muốn chỉnh/thiết kế UI/UX, đổi màu/layout, cải thiện trải nghiệm, kiểm thử giao diện bằng trình duyệt, chụp màn hình, hoặc audit hiệu năng/accessibility cho trang chat và trang log.
---

# Web UI/UX — Trợ lý Lab Hệ nhúng (Flask)

Giao diện web của project nằm GỌN trong một file: **`webapp/app.py`**. Không có
asset ngoài — HTML/CSS/JS được nhúng dạng chuỗi và phục vụ qua
`render_template_string`.

## Cấu trúc UI (nơi sửa)
- `_HTML` (trong `webapp/app.py`) — trang chat chính (`/`): header chọn
  mode/provider, khu hội thoại, ô nhập, khối `🛠️ tool calls` (Observation).
- `_LOGS_HTML` — trang xem log server (`/logs`), đọc từ `/api/logs`.
- **Design tokens** = biến CSS trong `:root` (đổi theme ở đây, đừng hardcode màu):
  `--bg, --card, --line, --txt, --mut, --acc, --user, --bot`.
- JS chính: `send()` (gọi `/api/ask`), `fillAnswer()`, `traceHtml()` (render tool),
  `metricsHtml()`. Stream từng bước tool qua `/api/ask_stream` (SSE).

## Quy tắc khi sửa UI/UX
1. **Chỉ sửa trong `webapp/app.py`** (chuỗi `_HTML`/`_LOGS_HTML`); giữ nguyên hành vi
   API (`/api/ask`, `/api/ask_stream`, `/api/logs`).
2. **Dùng biến CSS** sẵn có cho màu/spacing để theme nhất quán; không thêm thư viện
   ngoài (giữ "1 file, không asset ngoài").
3. **Responsive**: layout dùng fl*ex*; kiểm tra ở khổ hẹp (mobile) lẫn rộng.
4. **Accessibility**: giữ tương phản đủ, có `aria-label`/`title` cho nút icon, focus
   thấy được, `Enter` gửi / `Shift+Enter` xuống dòng (đã có).
5. **An toàn XSS**: mọi nội dung từ server phải qua `escapeHtml()` trước khi chèn
   (như `fillAnswer`/`traceHtml` đang làm) — KHÔNG nội suy chuỗi thô vào innerHTML.
6. **Flask không hot-reload**: sau khi sửa phải **chạy lại** server và **Ctrl+F5**.

## Chạy & xem
```bash
python -m webapp.app                 # http://127.0.0.1:5000
python -m webapp.app --port 8000 --provider google
```

## Kiểm thử / audit bằng MCP (đã cấu hình trong .mcp.json)
- **playwright** — điều khiển trình duyệt thật để kiểm thử UI: mở trang, gõ câu hỏi,
  bấm Gửi, chờ phản hồi, **chụp màn hình** (snapshot/screenshot), kiểm tra khối tool
  hiện đúng. Quy trình gợi ý:
  1. Chạy server (`python -m webapp.app`) ở cổng riêng (vd 5070).
  2. `browser_navigate` tới `http://127.0.0.1:5070/`.
  3. `browser_snapshot` để lấy cây accessibility; `browser_type` vào ô câu hỏi;
     `browser_click` nút Gửi; `browser_wait_for` câu trả lời; `browser_take_screenshot`.
- **chrome-devtools** — audit sâu: `lighthouse_audit` (performance/accessibility/SEO),
  `performance_start_trace`, kiểm tra console/network. Dùng để chấm điểm UX & tối ưu.

## Lưu ý provider khi kiểm thử
- Chọn **provider** ở dropdown: `openai`/`google` nhanh; `local` (Phi-3) chạy CPU
  rất chậm và UI **đã bỏ giới hạn thời gian** chờ (xem `TIMEOUT_MS` trong `_HTML`).
- Câu hỏi nhiều tool cần `agent max_steps` đủ lớn (web đặt **10** ở `app.py`).
- Để giao diện hiện khối tool, chọn **ReAct Agent** hoặc **So sánh** (Chatbot không
  gọi tool).

## Tham chiếu nhanh
- Trang chat: `GET /` · Hỏi: `POST /api/ask` · Stream: `GET /api/ask_stream` (SSE)
- Trang log: `GET /logs` · Dữ liệu log: `GET /api/logs?date=&event=&limit=`
- Khối tool trả về: hàm `traceHtml(trace)` (mỗi bước: `#step tool(args)` + Observation)
