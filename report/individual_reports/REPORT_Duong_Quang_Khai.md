# Individual Report: Lab 3 — Chatbot vs ReAct Agent

- **Student Name**: Dương Quang Khải _(kiểm tra lại nếu sai)_
- **Student ID**: 2A202600708 _(lấy từ branch git — sửa nếu sai)_
- **Date**: 2026-06-01
- **My area**: ReAct agent loop (`src/agent/agent.py`) + Telemetry & Evaluation
  (`src/telemetry/`, `scripts/analyze_logs.py`, `scripts/ablation.py`).

---

## I. Technical Contribution (15 Points)

Tôi phụ trách **bộ não điều khiển của agent** và **toàn bộ lớp đo lường/đánh giá**.

### Modules Implemented

| Module | Vai trò của tôi |
| :--- | :--- |
| `src/agent/agent.py` | Cài vòng ReAct `run()` + toàn bộ guardrail (parser, loop-guard, retry, sanitize, fallback) |
| `src/telemetry/metrics.py` | `PerformanceTracker` + tính **cost thật theo model** (bỏ mock) |
| `src/telemetry/logger.py` | Structured JSON logger (file luôn full, console có thể `silence_console()`) |
| `scripts/analyze_logs.py` | Parser log → dashboard Chatbot vs Agent (latency P50/P95/P99, token, loop count, success rate) |
| `scripts/ablation.py` | Thí nghiệm bật/tắt guardrail, đo định lượng |
| `tests/test_agent_loop.py` | 3 ca test vòng lặp bằng mock LLM (không cần API key) |

### Code Highlights (đóng góp cụ thể, có dẫn dòng)

1. **Parser 2 tầng cho model yếu** — model nhỏ hay bỏ ngoặc (`Action: get_lab_objective 1`).
   Tôi thêm regex nới lỏng làm tầng dự phòng sau regex chặt:
   - `agent.py:29` `_ACTION_RE` (chặt) → `agent.py:32` `_ACTION_LOOSE_RE` (nới).
2. **Khôi phục tham số rỗng** (`agent.py:188`): khi model phát `search_lab_docs()`
   rỗng, lấy số lab trong câu hỏi (hoặc cả câu) làm query → vẫn truy được dữ liệu.
3. **Loop-guard** (`agent.py:198–206`): đếm `(tool,args)` lặp; vượt ngưỡng thì chèn
   Observation nhắc kết luận thay vì đốt hết `max_steps`.
4. **Retry + exponential backoff** (`agent.py:104–126`): bọc lời gọi LLM, lỗi
   429/timeout không làm sập vòng lặp.
5. **Fallback grounded** (`agent.py:219, 226`): khi model không kết luận, trả về
   Observation chính xác cuối thay vì lỗi suông.
6. **Cost thật** (`metrics.py::_calculate_cost`): tính riêng giá input/output theo
   bảng `_PRICING` (gpt-4o, gemini-1.5-flash, local=0).

### Cách code của tôi tương tác với ReAct loop

`run()` là vòng `while steps < max_steps`: mỗi vòng gọi `_generate_with_retry`
(lớp telemetry của tôi ghi `LLM_METRIC` qua `tracker.track_request`), parse output
bằng regex của tôi, nếu là Action thì `_execute_tool` → ghi `AGENT_OBSERVATION`,
nếu là Final Answer thì thoát. Mọi nhánh lỗi (loop-guard/timeout/llm_failed) đều
phát event để `analyze_logs.py` tổng hợp lại — tức **phần điều khiển và phần đo
lường của tôi khớp nhau end-to-end**.

---

## II. Debugging Case Study (10 Points)

### Problem Description
Agent đôi khi **kẹt lặp**: model phát đúng một `Action: search_lab_docs(led)` lặp
đi lặp lại, không bao giờ ra `Final Answer` → đốt hết `max_steps`, lãng phí cả
lời gọi LLM lẫn lần thực thi tool.

### Log Source (`logs/2026-06-01.log`, trace thật)
```
AGENT_STEP   step=1  Action: search_lab_docs(led)   -> AGENT_OBSERVATION (kết quả LED)
AGENT_STEP   step=2  Action: search_lab_docs(led)   -> AGENT_OBSERVATION (TRÙNG y hệt)
AGENT_STEP   step=3  Action: search_lab_docs(led)   -> AGENT_LOOP_GUARD
                                                    -> AGENT_TIMEOUT (steps=3)
AGENT_END    answer = (Observation chính xác cuối)  # fallback grounded
```

### Diagnosis
Không phải lỗi tool hay parser — mà là **model yếu không nhận ra đã đủ thông tin**.
Observation lặp y hệt nhưng model vẫn lặp Action. Đây là lỗi *reasoning/termination*,
không phải lỗi *parsing*. Telemetry giúp tôi phân biệt điều này: nếu là parser
lỗi sẽ thấy `nonexistent tool`/Observation rỗng; ở đây Observation đúng và đầy đủ,
chỉ là model không kết luận.

### Solution
1. **Loop-guard** (`agent.py:200`): đếm Action trùng, vượt `max_repeated_actions`
   thì chèn Observation "Hãy đưa ra Final Answer dựa trên các Observation đã có".
2. **Fallback grounded** (`agent.py:226`): nếu vẫn không kết luận, trả về
   Observation chính xác cuối → người dùng vẫn nhận đúng dữ liệu LED của Lab 1.

### Đo lường hiệu quả (ablation thật — `scripts/ablation.py`)
| Config | LLM calls | Tool execs | Grounded |
| :--- | ---: | ---: | ---: |
| no guard, max_steps=6 | 6 | 6 | yes |
| + loop-guard (repeat>2) | 6 | **2** | yes |
| + loop-guard + max_steps=3 | **3** | **2** | yes |

→ loop-guard cắt **4/6 lần thực thi tool dư thừa**; siết `max_steps` cắt LLM calls
**6→3**. Thất bại được biến thành **xuống cấp êm**, không phải sập.

---

## III. Personal Insights: Chatbot vs ReAct (10 Points)

1. **Reasoning**: khối `Thought` cho phép agent **phân rã** câu hỏi. Ví dụ thật
   (log, gpt-4o): câu "mục đích + chuẩn bị Lab 2" được tách thành 2 Action
   (`get_lab_preparation` → `get_lab_objective`) rồi mới tổng hợp — chatbot trả
   một phát nên dễ thiếu/sai một vế.

2. **Reliability — khi nào Agent *kém hơn* Chatbot**: với câu chit-chat/tổng quát,
   agent **tốn ~3× token/task** (323.7 vs 108.2 — số thật từ `analyze_logs.py`) mà
   không thêm giá trị, lại có nguy cơ kẹt lặp (§II). Tức **agentic không miễn phí**:
   nó chỉ đáng khi có tool truy nguồn. Một insight phản trực giác từ telemetry:
   **P50 latency của agent (1.6s) lại *thấp hơn* chatbot (2.65s)** vì agent đi
   đường tool ngắn còn chatbot để model "nghĩ" dài.

3. **Observation định hướng bước sau**: chính vì Observation lặp y hệt mà model
   *không* đổi hành vi đã sinh ra bug ở §II — cho thấy chất lượng vòng lặp phụ
   thuộc model có biết "đọc" Observation hay không. Guardrail của tôi tồn tại
   chính vì **không thể tin model luôn phản ứng đúng với feedback môi trường**.

> Kết luận cá nhân: giá trị của ReAct **không nằm ở model mà ở grounding qua tool**.
> "Đọc tài liệu để trả lời" xảy ra ở `loader.open(...)`, LLM chỉ tổng hợp.

---

## IV. Future Improvements (5 Points)

- **Scalability**: tách `_execute_tool` sang **hàng đợi async** để chạy song song
  nhiều tool/nhiều phiên; thay knowledge JSON bằng **vector DB** cho RAG khi tài
  liệu lớn dần.
- **Safety**: thêm một **Supervisor LLM** audit chuỗi Action trước khi thực thi
  (chặn hành vi lạ), bổ sung cho loop-guard hiện tại.
- **Performance / Retrieval**: **hybrid retrieval** (keyword `search_lab_docs` +
  embedding) cho câu hỏi diễn đạt khác từ khóa; thêm **per-tool telemetry**
  (latency, hit/miss) để loại tool chết.
- **Termination**: thêm few-shot "khi Observation đã chứa câu trả lời → Final
  Answer ngay" để giảm phụ thuộc vào loop-guard (xử lý gốc rễ bug §II).

---

> Nộp: file đã đổi tên `REPORT_Duong_Quang_Khai.md` trong `report/individual_reports/`.
