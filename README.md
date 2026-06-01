# Lab 3: Chatbot vs ReAct Agent (Industry Edition)

Welcome to Phase 3 of the Agentic AI course! This lab focuses on moving from a simple LLM Chatbot to a sophisticated **ReAct Agent** with industry-standard monitoring.

## 🚀 Getting Started

### 1. Setup Environment
Copy the `.env.example` to `.env` and fill in your API keys:
```bash
cp .env.example .env
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Directory Structure
- `src/tools/`: Extension point for your custom tools.

## 🏠 Running with Local Models (CPU)

If you don't want to use OpenAI or Gemini, you can run open-source models (like Phi-3) directly on your CPU using `llama-cpp-python`.

### 1. Download the Model
Download the **Phi-3-mini-4k-instruct-q4.gguf** (approx 2.2GB) from Hugging Face:
- [Phi-3-mini-4k-instruct-GGUF](https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf)
- Direct Download: [phi-3-mini-4k-instruct-q4.gguf](https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf)

### 2. Place Model in Project
Create a `models/` folder in the root and move the downloaded `.gguf` file there.

### 3. Update `.env`
Change your `DEFAULT_PROVIDER` and set the path:
```env
DEFAULT_PROVIDER=local
LOCAL_MODEL_PATH=./models/Phi-3-mini-4k-instruct-q4.gguf
```

## 🤖 Ứng dụng: Trợ lý Lab môn Hệ nhúng (IT4210)

Kịch bản triển khai của repo này là một **trợ lý hỗ trợ sinh viên môn Hệ nhúng**,
trả lời về **mục đích lab → chuẩn bị lab → hướng dẫn bài tập**. Kiến thức được trích
từ 3 tài liệu trong `docs/` (Lab 1: GPIO/Interrupt/Timer, Lab 2: I2C/SPI, Lab 3:
FreeRTOS/TouchGFX) vào `data/embedded_labs.json`.

### Chạy thử
```bash
# ReAct Agent (có công cụ tra cứu lab)
python main.py agent --once "Lab 2 cần chuẩn bị gì và mục đích là gì?"

# Chatbot baseline (không công cụ — để thấy giới hạn)
python main.py chatbot --once "Sơ đồ chân ghép nối RC522 là gì?"

# So sánh Chatbot vs Agent trên cùng câu hỏi
python main.py compare "Hướng dẫn giải mã hồng ngoại NEC ở Lab 1?"

# Chế độ tương tác
python main.py agent
```
Chọn provider qua `.env` (`DEFAULT_PROVIDER=local|openai|google`, mặc định **local**
chạy offline với Phi-3) hoặc cờ `--provider`.

### Công cụ của Agent (`src/tools/`)
| Tool | Chức năng |
| :--- | :--- |
| `get_lab_objective` | Mục đích/mục tiêu của Lab 1/2/3 |
| `get_lab_preparation` | Phần cứng, phần mềm, tài liệu cần chuẩn bị |
| `get_exercise_guide` | Các phần hướng dẫn + bài tập (lọc theo chủ đề) |
| `search_lab_docs` | Tìm kiếm toàn văn (không phân biệt dấu) |
| `lookup_pin_mapping` | Sơ đồ chân ghép nối (rc522, hs0038, led, ds1307...) |
| `web_search`, `fetch_url` | Tra cứu trên mạng (datasheet, chuẩn giao tiếp) |

### Test (không cần API key)
```bash
python tests/test_tools.py        # kiểm thử công cụ + knowledge base
python tests/test_agent_loop.py   # kiểm thử vòng lặp ReAct (mock LLM)
```

### MCP & Skill
- `.mcp.json` cấu hình sẵn các MCP server:
  - `fetch`, `git`, `filesystem`, `sequential-thinking`, `context7` — tra cứu tài
    liệu/GitHub/mạng, thao tác repo.
  - `playwright`, `chrome-devtools` — **kiểm thử UI/UX web** (mở trình duyệt thật,
    click/gõ/chụp màn hình, audit Lighthouse/hiệu năng cho `webapp/`).
- Skill Claude Code trong `.claude/skills/`:
  - `lab-assistant/` — dùng trợ lý lab (mục đích/chuẩn bị/sơ đồ chân/bài tập).
  - `web-uiux/` — chỉnh & kiểm thử **giao diện web** (Flask chat UI trong
    `webapp/app.py`): design tokens, responsive, accessibility, dùng MCP để test.

## 🎯 Lab Objectives

1.  **Baseline Chatbot**: Observe the limitations of a standard LLM when faced with multi-step reasoning.
2.  **ReAct Loop**: Implement the `Thought-Action-Observation` cycle in `src/agent/agent.py`.
3.  **Provider Switching**: Swap between OpenAI and Gemini seamlessly using the `LLMProvider` interface.
4.  **Failure Analysis**: Use the structured logs in `logs/` to identify why the agent fails (hallucinations, parsing errors).
5.  **Grading & Bonus**: Follow the [SCORING.md](file:///Users/tindt/personal/ai-thuc-chien/day03-lab-agent/SCORING.md) to maximize your points and explore bonus metrics.

## 🛠️ How to Use This Baseline
The code is designed as a **Production Prototype**. It includes:
- **Telemetry**: Every action is logged in JSON format for later analysis.
- **Robust Provider Pattern**: Easily extendable to any LLM API.
- **Clean Skeletons**: Focus on the logic that matters—the agent's reasoning process.

---

*Happy Coding! Let's build agents that actually work.*
