# Group Report: Lab 3 — Production-Grade Agentic System

- **Team Name**: _[điền tên nhóm]_
- **Team Members**: _[Thành viên 1, Thành viên 2, ...]_
- **Deployment Date**: 2026-06-01
- **Application**: Trợ lý hỗ trợ sinh viên môn Hệ nhúng (IT4210) — trả lời
  **mục đích lab → chuẩn bị lab → hướng dẫn bài tập** cho 3 bài thực hành.

> **Reproducibility**: mọi số liệu trong báo cáo này sinh trực tiếp từ telemetry
> thật trong `logs/` bằng `python scripts/analyze_logs.py` và
> `python scripts/ablation.py`. Không có số liệu bịa.

---

## 0. Rubric Traceability (SCORING.md → bằng chứng)

| Tiêu chí (điểm) | Bằng chứng trong repo |
| :--- | :--- |
| Chatbot Baseline (2) | `src/chatbot.py` — LLM thuần, no tool, no loop (§1, §6.1) |
| Agent v1 Working (7) | `src/agent/agent.py` vòng ReAct + 7 tool (§2, §6.2 trace thành công) |
| Agent v2 Improved (7) | §5 bảng v1→v2 + `tool_design_evolution.md` |
| Tool Design Evolution (4) | `tool_design_evolution.md` (9 thay đổi spec, có file:line) |
| Trace Quality (9) | §6 — 1 trace thành công + 2 trace thất bại, có RCA |
| Evaluation & Analysis (7) | §3 dashboard số liệu thật + §4 Chatbot vs Agent |
| Flowchart & Insight (5) | `docs/doc_reading_flow.html` + §8 learning points |
| Code Quality (4) | modular `core/agent/tools/knowledge/telemetry`, test offline |
| **Bonus** Extra Monitoring (+3) | `metrics.py` cost thật theo model (§3.2) |
| **Bonus** Extra Tools (+2) | `web_tools.py`: `web_search`, `fetch_url` |
| **Bonus** Failure Handling (+3) | retry+backoff, loop-guard, sanitize (§5, §7) |
| **Bonus** Ablation (+2) | `scripts/ablation.py` + `ablation_results.md` (§9) |
| **Bonus** Live Demo (+5) | `webapp/app.py` (`python -m webapp.app`) — demo trực tiếp |

---

## 1. Executive Summary

- **Goal**: thay một chatbot LLM thuần (hay đoán bừa sơ đồ chân, mục đích bài)
  bằng một **ReAct Agent** truy xuất tài liệu lab có thật trước khi trả lời.
- **Telemetry thật (gpt-4o, loại mock)**: agent xử lý **72 lượt hỏi**, trung bình
  **1.78 vòng** Thought→Action→Observation/lượt, **P50 latency 1.6s**, và đạt
  câu trả lời **grounded** (trích đúng chân PE4/PE2/PE5/PE6 cho RC522 — thứ mà
  chatbot không có trong tham số mô hình).
- **Key Outcome**: với câu hỏi tra cứu chính xác (sơ đồ chân, mục đích từng bài),
  agent trả lời **đúng nguồn `data/embedded_labs.json`**, còn chatbot chỉ dựa vào
  trí nhớ mô hình nên không đảm bảo đúng. Đổi lại, agent tốn **~3× token** và
  thêm độ trễ do vòng lặp + tool.

---

## 2. System Architecture & Tooling

### 2.1 ReAct Loop Implementation

Vòng `Thought → Action → Observation` cài trong `src/agent/agent.py` (`run()`,
dòng 128–235). Sơ đồ trực quan: **`docs/doc_reading_flow.html`** (mở bằng trình
duyệt).

```
Question
  └─> LLM.generate ──> parse output
        ├─ "Final Answer:"  ─────────────> trả người dùng
        └─ "Action: tool(args)" ─> _execute_tool ─> đọc data/embedded_labs.json
                                       └─> Observation ──┐
        ^──────────────── lặp lại (≤ max_steps, loop-guard) ──┘
```

Điểm mấu chốt: **việc "đọc tài liệu để trả lời" nằm ở tool**
(`lab_tools.search_lab_docs` → `loader.load()` → `open(embedded_labs.json)`),
**không** nằm ở LLM provider. LLM chỉ nhận `Observation` rồi tổng hợp.

### 2.2 Tool Definitions (Inventory)

| Tool Name | Input Format | Use Case |
| :--- | :--- | :--- |
| `get_lab_objective` | `string` (1/2/3) | Mục đích bài lab |
| `get_lab_preparation` | `string` (1/2/3) | Phần cứng / phần mềm / tài liệu |
| `get_exercise_guide` | `string` (`id topic`) | Hướng dẫn + bài tập, lọc theo chủ đề |
| `search_lab_docs` | `string` | Full-text, không phân biệt dấu |
| `lookup_pin_mapping` | `string` | Sơ đồ chân theo lab/linh kiện |
| `web_search` | `string` | DuckDuckGo (datasheet, chuẩn giao tiếp) — *bonus* |
| `fetch_url` | `string` | Tải nội dung 1 URL — *bonus* |

Chi tiết tiến hóa spec: xem **`tool_design_evolution.md`**.

### 2.3 LLM Providers Used

- **Primary (đo hiệu năng)**: OpenAI `gpt-4o`.
- **Secondary**: Google `gemini-1.5-flash`.
- **Offline/free**: `local` (Phi-3-mini qua `llama-cpp-python`) — chạy không tốn API.
- Trừu tượng hóa qua `LLMProvider` + `provider_factory.py` (Strategy pattern):
  đổi provider chỉ bằng `.env` (`DEFAULT_PROVIDER`) hoặc cờ `--provider`.

---

## 3. Telemetry & Performance Dashboard

Số liệu sinh bằng `python scripts/analyze_logs.py`. Mỗi `LLM_METRIC` được gán cho
mode (chatbot/agent) đang chạy gần nhất.

### 3.1 Bảng chính — provider OpenAI `gpt-4o` (đại diện production)

| Metric | Chatbot | ReAct Agent |
| :--- | ---: | ---: |
| Tasks (user queries) | 13 | 72 |
| LLM requests | 5 | 33 |
| Avg latency | 4690.0 ms | 2376.8 ms |
| Latency P50 | 2651 ms | 1619 ms |
| Latency P95 | 10533 ms | 4514 ms |
| Latency P99 | 10533 ms | 12196 ms |
| Total tokens | 1406 | 23306 |
| Avg tokens / task | 108.2 | 323.7 |
| Est. cost (USD) | $0.014 | $0.233 |
| Avg loop count | 0.0 | 1.78 |
| Max loop count | 0 | 6 |
| Loop-guard trips | 0 | 8 |
| Timeouts | 0 | 7 |
| Hard failures | 2 | 11 |
| Success rate | 84.6 % | 75.0 % |

> **Đọc số**: agent **P50 thấp hơn chatbot** vì nhiều câu agent chỉ cần 1 vòng tool
> ngắn, trong khi chatbot để gpt-4o tự "nghĩ" dài. Nhưng **token/task của agent
> cao ~3×** (đính kèm Observation vào transcript) → chi phí cao hơn. Đây là đánh
> đổi **độ chính xác/grounding ⇄ chi phí** kinh điển của agentic.
>
> *Caveat trung thực*: các dòng loop-guard/timeout/failures không gắn nhãn provider
> nên là **tổng trên mọi provider** (gồm cả local/mock yếu hơn), không chỉ gpt-4o;
> vì vậy success-rate cột agent là cận dưới bi quan.

### 3.2 Cost monitoring (bonus — Extra Monitoring)

`metrics.py::_calculate_cost` đã thay mock bằng **bảng giá thật**, tính riêng giá
input/output theo model:

| Model | $/1K in | $/1K out | Ví dụ 1000in+500out |
| :--- | ---: | ---: | ---: |
| gpt-4o | 0.0025 | 0.0100 | **$0.0075** |
| gemini-1.5-flash | 0.000075 | 0.0003 | **$0.000225** |
| local (Phi-3) | 0 | 0 | **$0.0** |

→ cho phép so sánh ROI thật: 1 câu gpt-4o đắt hơn gemini-flash ~33×, local miễn phí.

---

## 4. Evaluation & Analysis — Chatbot vs Agent

| Case (câu hỏi) | Chatbot | Agent | Winner |
| :--- | :--- | :--- | :--- |
| Câu đơn giản ("Lab 3 chuẩn bị gì?") | Trả lời được từ trí nhớ | Trả lời được (1–2 vòng) | Draw |
| **Sơ đồ chân RC522 ở Lab 2** | Không có dữ kiện chân chính xác trong tham số mô hình → không đảm bảo | Trả đúng **PE4/PE2/PE5/PE6, RST=3V** từ KB | **Agent** |
| Đa bước (mục đích + chuẩn bị cùng lúc) | Một câu trả lời, dễ thiếu/sai | 3 vòng: prep → objective → tổng hợp (§6.1) | **Agent** |
| Tốc độ/chi phí câu tầm thường | Rẻ hơn, ít token | Tốn ~3× token | **Chatbot** |

**Kết luận đánh giá**: Agent thắng ở **độ chính xác có dẫn nguồn** cho câu hỏi tra
cứu domain-specific; Chatbot thắng ở **chi phí/độ trễ** cho câu hỏi tổng quát.
Chiến lược production hợp lý: **router** — câu tra cứu → agent, câu chit-chat →
chatbot.

---

## 5. Agent v1 → v2 (Improved) & Failure Handling

| Lỗi quan sát ở v1 (từ log) | Bản vá v2 | Code |
| :--- | :--- | :--- |
| Model nhỏ bỏ ngoặc: `Action: get_lab_objective 1` → parse fail | regex nới lỏng `_ACTION_LOOSE_RE` | `agent.py:32` |
| Args rỗng/rác → query rỗng → Observation rỗng → lặp | khôi phục arg từ câu hỏi (số lab/cả câu) | `agent.py:188` |
| Lặp đúng một Action vô hạn → đốt hết step | **loop-guard** đếm `(tool,args)` lặp | `agent.py:198-206` |
| LLM/API lỗi (429, timeout) làm sập tiến trình | **retry + exponential backoff** | `agent.py:104-126` |
| Input/arg quá dài → tràn context | sanitize cắt `max_input_chars`/`max_arg_chars` | `agent.py:93-102, 193` |
| Model yếu không kết luận | fallback về **Observation chính xác cuối** | `agent.py:219, 225` |
| Tool ném exception → chết loop | bọc try/except mỗi tool | `agent.py:241-245` |

→ Hiệu quả định lượng của các guardrail này: xem **§9 Ablation**.

---

## 6. Trace Quality (Root Cause Analysis)

### 6.1 ✅ Trace THÀNH CÔNG (thật, gpt-4o, 3 vòng) — log 2026-06-01

Câu hỏi: *"Lab 2 cần chuẩn bị gì và mục đích là gì?"*

```
Step 1  Thought: cần tra phần chuẩn bị Lab 2.
        Action: get_lab_preparation(2)
        Observation: Bài TH 2 (I2C, SPI) — HW: STM32F429I, Tiny RTC(DS1307+AT24C32),
                     OLED SH1106, RC522... | SW: STM32CubeIDE, Hercules | Tài liệu: ...
        [LLM_METRIC openai gpt-4o tok=777 lat=996ms]
Step 2  Thought: đã có chuẩn bị, giờ cần mục đích.
        Action: get_lab_objective(2)
        Observation: Mục đích — ghép nối RFID/RTC/OLED; tìm hiểu I2C, SPI; mô phỏng
                     đóng/mở cửa bằng thẻ RFID...
        [LLM_METRIC openai gpt-4o tok=1253 lat=2890ms]
Step 3  Final Answer: (tổng hợp đủ Mục đích + Chuẩn bị, trích đúng linh kiện & tài liệu)
        [AGENT_END steps=3]
```

**Vì sao tốt**: agent **phân rã** câu hỏi 2 ý thành 2 lần gọi tool đúng, rồi tổng
hợp; mọi chi tiết phần cứng/tài liệu đều **truy ngược được** về KB → không bịa.

### 6.2 ❌ Trace THẤT BẠI A — vòng lặp lặp Action → loop-guard → timeout

```
Step 1  Action: search_lab_docs(led)   -> Observation: (kết quả LED Lab 1)
Step 2  Action: search_lab_docs(led)   -> Observation: (TRÙNG y hệt)
Step 3  Action: search_lab_docs(led)   -> AGENT_LOOP_GUARD (đã lặp > ngưỡng)
                                       -> AGENT_TIMEOUT (steps=3)
        AGENT_END: trả về Observation chính xác cuối cùng (fallback grounded)
```

- **Root cause**: model (yếu) không nhận ra đã đủ thông tin, lặp đúng một Action.
- **Vì sao không thảm họa**: `loop-guard` (`agent.py:200`) chặn thực thi tool dư
  thừa và nhắc kết luận; khi vẫn không kết luận, `max_steps` cắt và **fallback về
  Observation chính xác** (`agent.py:225`) → người dùng vẫn nhận dữ liệu đúng,
  không phải thông báo lỗi suông.
- **Cách khắc phục thêm**: thêm few-shot "khi Observation đã chứa câu trả lời, ra
  Final Answer ngay" vào system prompt; hoặc hạ `max_repeated_actions`.

### 6.3 ❌ Trace THẤT BẠI B — provider lỗi (429/timeout)

```
AGENT_LLM_FAILED  (lần 1) -> sleep backoff -> retry
AGENT_LLM_FAILED  (lần 2) -> sleep 2× -> retry
AGENT_LLM_FAILED  -> AGENT_END status=llm_failed
  -> trả: "Xin lỗi, hệ thống AI tạm thời không phản hồi..."
```

- **Root cause**: lỗi phía dịch vụ (hết quota/timeout), ngoài tầm kiểm soát.
- **Xử lý**: `_generate_with_retry` (`agent.py:104`) thử lại với exponential
  backoff; cạn lượt → **thông điệp thân thiện**, không ném exception ra ngoài.

---

## 7. Production Readiness Review

- **Security / Safety**: sanitize input người dùng (`agent.py:93`) và cắt tham số
  tool (`agent.py:193`) chống prompt quá khổ / lạm dụng; `fetch_url` chỉ nhận
  `http(s)://`.
- **Guardrails**: `max_steps` (chặn billing vô hạn), `loop-guard` (chặn lặp),
  retry+backoff (chịu lỗi mạng), tool try/except (cách ly sự cố).
- **Observability**: mọi sự kiện ghi JSON có cấu trúc vào `logs/` →
  `analyze_logs.py` ra dashboard; console có thể `silence_console()` cho web.
- **Scaling**: chuyển KB sang **vector DB + hybrid retrieval**; thêm **router**
  chatbot/agent; tách tool execution sang hàng đợi async; cân nhắc LangGraph cho
  nhánh điều khiển phức tạp.

---

## 8. Flowchart & Group Insights

- **Flowchart**: `docs/doc_reading_flow.html` (ReAct loop + chỗ đọc doc được tô màu).
- **Learning points của nhóm**:
  1. **Agent ≠ luôn tốt hơn chatbot.** Nó chỉ thắng khi có *tool truy nguồn*; câu
     chit-chat thì chatbot rẻ và đủ.
  2. **Tool contract phải "boring".** Single-string arg + plain-text Observation
     giúp cả model yếu (Phi-3) chạy được; JSON-arg là nguồn lỗi lớn nhất ở v1.
  3. **Grounding nằm ở tool, không ở model.** "Đọc tài liệu để trả lời" xảy ra ở
     `loader.open(...)`, LLM chỉ tổng hợp Observation.
  4. **Guardrail biến thất bại thành xuống cấp êm.** Nhờ fallback-last-observation,
     ngay cả khi timeout người dùng vẫn nhận dữ liệu đúng (xem §6.2, §9).
  5. **Đo mới biết.** P50 agent thật ra *thấp hơn* chatbot — trái trực giác, chỉ
     lộ ra khi phân tích telemetry.

---

## 9. Ablation Studies & Experiments

Sinh bằng `python scripts/ablation.py` (mock LLM, không cần API). Kết quả lưu tại
`report/group_report/ablation_results.md`.

### Exp 1+2 — model lặp Action vô hạn (worst case)

| Config | LLM calls | Tool execs | Grounded |
| :--- | ---: | ---: | ---: |
| baseline (no guard, max_steps=6) | 6 | 6 | yes |
| + loop-guard (repeat>2) | 6 | **2** | yes |
| + loop-guard + max_steps=3 | **3** | **2** | yes |

→ **loop-guard cắt 4/6 lần thực thi tool dư thừa**; **max_steps siết LLM calls
6→3**. Mọi cấu hình vẫn trả **grounded** nhờ fallback.

### Exp 3 — model ngoan (kết luận ở bước 3)

| Config | LLM calls | Tool execs | Grounded |
| :--- | ---: | ---: | ---: |
| max_steps=2 (quá chặt) | 2 | 2 | yes |
| max_steps=5 (đủ) | 3 | 2 | yes |

→ `max_steps` quá nhỏ cắt trước khi model kịp Final Answer (nhưng fallback vẫn cứu
được dữ liệu); `max_steps=5` cho kết luận sạch trong 3 calls. **Bài học: chọn
`max_steps` đủ rộng cho chuỗi suy luận dài nhất + 1.**

---

## 10. How to Reproduce

```bash
# Dashboard số liệu thật
python scripts/analyze_logs.py --exclude-mock --markdown
python scripts/analyze_logs.py --provider openai --markdown

# Ablation guardrail
python scripts/ablation.py

# Chạy thử agent vs chatbot
python main.py compare "Sơ đồ chân ghép nối RC522 ở Lab 2 là gì?"

# Demo web (bonus Live Demo)
python -m webapp.app   # rồi mở http://127.0.0.1:5000

# Test offline (không cần API key)
python tests/test_tools.py
python tests/test_agent_loop.py
```

---

> **"Fail Early, Learn Fast"** — §6 ưu tiên phân tích cả trace thất bại; §9 chứng
> minh định lượng rằng guardrail biến thất bại thành xuống cấp êm thay vì sập.
