import os
from threading import Lock

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

from src.core.gemini_provider import GeminiProvider
from src.core.local_provider import LocalProvider
from src.core.openai_provider import OpenAIProvider
from src.tools.data_lookup import search_data

load_dotenv()

app = Flask(__name__)
agent_lock = Lock()
_provider_cache = {}


def get_provider(provider_name: str):
    provider_name = provider_name.lower().strip()

    if provider_name in _provider_cache:
        return _provider_cache[provider_name]

    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing in .env")

        llm = OpenAIProvider(
            model_name=os.getenv("OPENAI_MODEL", os.getenv("DEFAULT_MODEL", "gpt-4o")),
            api_key=api_key,
        )

    elif provider_name == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing in .env")

        llm = GeminiProvider(
            model_name=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
            api_key=api_key,
        )

    elif provider_name == "local":
        llm = LocalProvider(
            model_path=os.getenv(
                "LOCAL_MODEL_PATH",
                "./models/Phi-3-mini-4k-instruct-q4.gguf",
            ),
        )

    else:
        raise ValueError(f"Unsupported provider: {provider_name}")

    _provider_cache[provider_name] = llm
    return llm


HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Local Data Chat</title>
  <style>
    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #343541;
      color: #ececf1;
    }

    .app {
      display: flex;
      height: 100vh;
      width: 100vw;
    }

    .sidebar {
      width: 260px;
      background: #202123;
      padding: 14px;
      border-right: 1px solid #2f3037;
    }

    .new-chat, select {
      width: 100%;
      padding: 12px;
      background: transparent;
      color: #ececf1;
      border: 1px solid #565869;
      border-radius: 6px;
      font-size: 14px;
    }

    .new-chat {
      cursor: pointer;
      text-align: left;
      margin-bottom: 10px;
    }

    select {
      background: #202123;
      cursor: pointer;
    }

    .sidebar-note {
      margin-top: 18px;
      font-size: 13px;
      color: #b4b4c0;
      line-height: 1.5;
    }

    .main {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }

    .header {
      height: 56px;
      display: flex;
      align-items: center;
      padding: 0 20px;
      border-bottom: 1px solid #444654;
      font-weight: 600;
    }

    .chat {
      flex: 1;
      overflow-y: auto;
      padding-bottom: 24px;
    }

    .message {
      display: flex;
      gap: 16px;
      padding: 22px max(24px, calc((100vw - 900px) / 2));
      line-height: 1.55;
      white-space: pre-wrap;
    }

    .message.user { background: #343541; }
    .message.assistant { background: #444654; }

    .avatar {
      width: 32px;
      height: 32px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 32px;
      font-size: 13px;
      font-weight: 700;
    }

    .user .avatar { background: #5b6ee1; }
    .assistant .avatar { background: #10a37f; }

    .content {
      max-width: 760px;
      overflow-wrap: anywhere;
    }

    .composer-wrap {
      padding: 16px 20px 24px;
      background: #343541;
    }

    .composer {
      max-width: 820px;
      margin: 0 auto;
      display: flex;
      align-items: flex-end;
      gap: 10px;
      background: #40414f;
      border: 1px solid #565869;
      border-radius: 10px;
      padding: 10px;
    }

    textarea {
      flex: 1;
      resize: none;
      min-height: 28px;
      max-height: 160px;
      border: 0;
      outline: 0;
      background: transparent;
      color: #ececf1;
      font-size: 15px;
      line-height: 1.45;
      font-family: inherit;
    }

    button.send {
      width: 36px;
      height: 36px;
      border: 0;
      border-radius: 6px;
      background: #ececf1;
      color: #202123;
      cursor: pointer;
      font-size: 18px;
      line-height: 1;
    }

    button.send:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    .hint {
      max-width: 820px;
      margin: 8px auto 0;
      color: #b4b4c0;
      font-size: 12px;
      text-align: center;
    }

    @media (max-width: 760px) {
      .sidebar { display: none; }
      .message { padding: 18px 14px; }
      .composer-wrap { padding: 12px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <button class="new-chat" onclick="newChat()">+ New chat</button>

      <select id="provider">
        <option value="local">Phi-3 Local</option>
        <option value="openai">OpenAI</option>
        <option value="gemini">Gemini</option>
      </select>

      <div class="sidebar-note">
        Local Data Chat<br>
        Trả lời dựa trên file trong thư mục <code>data/</code>.
      </div>
    </aside>

    <main class="main">
      <div class="header">Local Data Chat</div>

      <div id="chat" class="chat">
        <div class="message assistant">
          <div class="avatar">AI</div>
          <div class="content">Chào bạn. Mình sẽ trả lời dựa trên dữ liệu trong thư mục data. Bạn muốn hỏi gì?</div>
        </div>
      </div>

      <div class="composer-wrap">
        <div class="composer">
          <textarea id="question" rows="1" placeholder="Ví dụ: Lab 2 làm gì cụ thể?"></textarea>
          <button id="sendBtn" class="send" onclick="askAgent()">↑</button>
        </div>
        <div class="hint">Enter để gửi, Shift+Enter để xuống dòng. Chọn Phi-3 Local, OpenAI hoặc Gemini ở sidebar.</div>
      </div>
    </main>
  </div>

  <script>
    const chat = document.getElementById("chat");
    const questionInput = document.getElementById("question");
    const sendBtn = document.getElementById("sendBtn");
    const providerSelect = document.getElementById("provider");

    function addMessage(role, text) {
      const message = document.createElement("div");
      message.className = "message " + role;

      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = role === "user" ? "You" : "AI";

      const content = document.createElement("div");
      content.className = "content";
      content.textContent = text;

      message.appendChild(avatar);
      message.appendChild(content);
      chat.appendChild(message);
      chat.scrollTop = chat.scrollHeight;

      return content;
    }

    function newChat() {
      chat.innerHTML = "";
      addMessage("assistant", "Đã tạo chat mới. Hỏi mình về dữ liệu trong thư mục data nhé.");
      questionInput.value = "";
      questionInput.focus();
    }

    async function askAgent() {
      const question = questionInput.value.trim();
      const provider = providerSelect.value;

      if (!question) return;

      addMessage("user", question);
      questionInput.value = "";
      questionInput.style.height = "auto";

      sendBtn.disabled = true;
      const assistantBubble = addMessage("assistant", "Đang đọc dữ liệu và suy nghĩ...");

      try {
        const res = await fetch("/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({question, provider})
        });

        const data = await res.json();

        if (!res.ok) {
          assistantBubble.textContent = data.error || "Request failed.";
          return;
        }

        assistantBubble.textContent = data.answer;
      } catch (err) {
        assistantBubble.textContent = String(err);
      } finally {
        sendBtn.disabled = false;
        questionInput.focus();
      }
    }

    questionInput.addEventListener("input", () => {
      questionInput.style.height = "auto";
      questionInput.style.height = Math.min(questionInput.scrollHeight, 160) + "px";
    });

    questionInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        askAgent();
      }
    });
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    question = data.get("question", "").strip()
    provider_name = data.get("provider", "local").strip()

    if not question:
        return jsonify({"error": "Question is required."}), 400

    context = search_data(question)

    if context.startswith("NO_RELEVANT_DATA_FOUND"):
        return jsonify({
            "answer": "Không tìm thấy thông tin này trong dữ liệu.\n\n" + context,
            "provider": provider_name,
        })

    prompt = f"""
Bạn là trợ lý học tập chuyên đọc tài liệu thực hành kỹ thuật.

Nhiệm vụ:
- Chỉ trả lời dựa trên CONTEXT bên dưới.
- Trả lời bằng tiếng Việt rõ ràng, có cấu trúc.
- Không bịa thêm nội dung ngoài tài liệu.
- Nếu tài liệu không đủ thông tin, nói rõ phần nào chưa thấy trong dữ liệu.
- Ưu tiên diễn giải dễ hiểu cho sinh viên chuẩn bị làm bài thực hành.

Cách trình bày:
1. Tóm tắt ngắn gọn nội dung chính.
2. Các việc cần làm cụ thể.
3. Thiết bị, linh kiện, phần mềm hoặc kiến thức liên quan nếu CONTEXT có nhắc tới.
4. Kết quả cần đạt hoặc sản phẩm đầu ra nếu CONTEXT có nhắc tới.
5. Lưu ý quan trọng nếu có.

CONTEXT:
{context}

Câu hỏi của người dùng:
{question}

Trả lời:
""".strip()

    try:
        with agent_lock:
            llm = get_provider(provider_name)
            result = llm.generate(prompt)
            answer = result.get("content", "").strip()

        if not answer:
            answer = context

        return jsonify({"answer": answer, "provider": provider_name})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7860, debug=False)