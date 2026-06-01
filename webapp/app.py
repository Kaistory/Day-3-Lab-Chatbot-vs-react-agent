"""
Web UI đơn giản (Flask) để chạy project Trợ lý Lab Hệ nhúng (IT4210).

Chạy:
    pip install flask
    python -m webapp.app                 # mặc định http://127.0.0.1:5000
    python -m webapp.app --port 8000      # đổi cổng
    python -m webapp.app --provider local # ép provider (local|openai|google)

Provider mặc định lấy từ DEFAULT_PROVIDER trong .env (local Phi-3 nếu không đặt).
Model local được NẠP MỘT LẦN rồi cache để tránh nạp lại mỗi request (Phi-3 nạp chậm).
"""
import argparse
import sys
import time

# Vietnamese-safe stdout/stderr trên console Windows (cp1252) — phải chạy trước
# khi import các module tạo logging StreamHandler.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

from src.core.provider_factory import create_provider
from src.chatbot import Chatbot
from src.agent.agent import ReActAgent
from src.tools import TOOLS
from src.telemetry.metrics import tracker
from src.telemetry.logger import logger

load_dotenv()

# Web server: ghi log đầy đủ ra FILE (logs/) nhưng KHÔNG in log/lỗi ra console,
# tránh terminal bị ngập dump lỗi (vd 429 quota dài hàng chục dòng).
logger.silence_console()

app = Flask(__name__)


def _short_error(e: Exception, limit: int = 200) -> str:
    """Thông điệp lỗi gọn cho người dùng: chỉ dòng đầu, cắt ngắn (không dump nguyên khối)."""
    msg = str(e).splitlines()[0].strip() if str(e).strip() else type(e).__name__
    return msg[:limit] + ("…" if len(msg) > limit else "")


@app.errorhandler(Exception)
def _handle_uncaught(e):
    """Bắt mọi lỗi chưa xử lý -> trả JSON gọn, ghi file log, không lộ traceback."""
    logger.error(f"Lỗi web chưa bắt: {_short_error(e)}", exc_info=False)
    return jsonify({"error": "Đã có lỗi xảy ra phía máy chủ. Vui lòng thử lại."}), 500

# --- Cache provider/chatbot/agent theo tên provider (nạp model 1 lần) ----------
_CACHE = {}
_FORCED_PROVIDER = None  # đặt qua --provider khi khởi động (nếu có)


def _get_engines(provider_name=None):
    """Trả về (chatbot, agent, model_name) cho provider, tạo & cache nếu cần."""
    name = provider_name or _FORCED_PROVIDER or "default"
    if name not in _CACHE:
        llm = create_provider(provider_name or _FORCED_PROVIDER)
        _CACHE[name] = {
            "chatbot": Chatbot(llm),
            "agent": ReActAgent(llm, TOOLS, max_steps=6),
            "model": llm.model_name,
        }
    return _CACHE[name]


def _metrics_since(start_index):
    """Tổng hợp telemetry phát sinh từ start_index của tracker.session_metrics."""
    new = tracker.session_metrics[start_index:]
    return {
        "calls": len(new),
        "total_tokens": sum(m.get("total_tokens", 0) for m in new),
        "prompt_tokens": sum(m.get("prompt_tokens", 0) for m in new),
        "completion_tokens": sum(m.get("completion_tokens", 0) for m in new),
        "latency_ms": sum(m.get("latency_ms", 0) for m in new),
        "cost_estimate": round(sum(m.get("cost_estimate", 0.0) for m in new), 6),
    }


@app.route("/")
def index():
    return render_template_string(_HTML, tools=TOOLS)


@app.route("/api/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "agent").strip()
    provider = (data.get("provider") or "").strip() or None

    if not question:
        return jsonify({"error": "Vui lòng nhập câu hỏi."}), 400

    try:
        engines = _get_engines(provider)
    except Exception as e:
        logger.error(f"Init provider lỗi: {_short_error(e)}", exc_info=False)
        return jsonify({"error": f"Không khởi tạo được provider: {_short_error(e)}"}), 500

    started = time.time()
    start_index = len(tracker.session_metrics)
    try:
        if mode == "chatbot":
            result = {"chatbot": engines["chatbot"].ask(question)}
        elif mode == "compare":
            result = {
                "chatbot": engines["chatbot"].ask(question),
                "agent": engines["agent"].run(question),
            }
        else:  # agent
            result = {"agent": engines["agent"].run(question)}
    except Exception as e:
        # Lỗi đã được ghi vào file log; trả về trình duyệt thông điệp gọn, không dump.
        logger.error(f"Xử lý câu hỏi lỗi: {_short_error(e)}", exc_info=False)
        return jsonify({"error": f"Lỗi khi xử lý: {_short_error(e)}"}), 500

    return jsonify({
        "mode": mode,
        "model": engines["model"],
        "answers": result,
        "metrics": _metrics_since(start_index),
        "wall_ms": int((time.time() - started) * 1000),
    })


# --- Giao diện (1 trang HTML tĩnh, không cần asset ngoài) ----------------------
_HTML = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trợ lý Lab Hệ nhúng (IT4210)</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --line:#334155; --txt:#e2e8f0; --mut:#94a3b8; --acc:#38bdf8; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Segoe UI", sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:18px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--mut); font-size:13px; }
  main { max-width:880px; margin:0 auto; padding:24px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
  select, textarea, button { font:inherit; border-radius:8px; border:1px solid var(--line);
    background:var(--card); color:var(--txt); padding:10px 12px; }
  textarea { width:100%; min-height:72px; resize:vertical; }
  button { background:var(--acc); color:#04293a; border:none; font-weight:600; cursor:pointer; padding:10px 18px; }
  button:disabled { opacity:.6; cursor:wait; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-top:14px; }
  .card h3 { margin:0 0 8px; font-size:14px; color:var(--acc); }
  .ans { white-space:pre-wrap; line-height:1.5; }
  .metrics { display:flex; gap:18px; flex-wrap:wrap; color:var(--mut); font-size:12px; margin-top:6px; }
  .metrics b { color:var(--txt); }
  .err { color:#f87171; }
  details { margin-top:10px; color:var(--mut); font-size:13px; }
  code { background:#0b1220; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>🤖 Trợ lý Lab môn Hệ nhúng (IT4210)</h1>
  <p>Chatbot baseline vs ReAct Agent — chạy ngay trên trình duyệt</p>
</header>
<main>
  <div class="row">
    <select id="mode">
      <option value="agent">ReAct Agent (có tools)</option>
      <option value="chatbot">Chatbot (không tools)</option>
      <option value="compare">So sánh cả hai</option>
    </select>
    <select id="provider">
      <option value="">Provider mặc định (.env)</option>
      <option value="local">local (Phi-3)</option>
      <option value="openai">openai</option>
      <option value="google">google</option>
    </select>
  </div>
  <textarea id="q" placeholder="Ví dụ: Lab 2 cần chuẩn bị phần cứng gì? Sơ đồ chân RC522?"></textarea>
  <div class="row" style="margin-top:10px">
    <button id="send">Gửi</button>
    <span id="status" style="color:var(--mut);font-size:13px"></span>
  </div>
  <div id="out"></div>

  <details>
    <summary>{{ tools|length }} công cụ của agent</summary>
    <ul>{% for t in tools %}<li><code>{{ t.name }}</code> — {{ t.description }}</li>{% endfor %}</ul>
  </details>
</main>

<script>
const $ = (id) => document.getElementById(id);

async function send() {
  const question = $("q").value.trim();
  if (!question) { $("status").textContent = "Hãy nhập câu hỏi."; return; }
  const mode = $("mode").value, provider = $("provider").value;
  $("send").disabled = true;
  $("status").textContent = "Đang xử lý… (model local có thể mất vài giây)";
  $("out").innerHTML = "";
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ question, mode, provider })
    });
    const data = await res.json();
    if (!res.ok) { renderError(data.error || "Lỗi không xác định"); return; }
    renderAnswer(data);
  } catch (e) {
    renderError(String(e));
  } finally {
    $("send").disabled = false;
    $("status").textContent = "";
  }
}

function renderError(msg) {
  $("out").innerHTML = `<div class="card err">⚠️ ${escapeHtml(msg)}</div>`;
}

function card(title, text) {
  return `<div class="card"><h3>${title}</h3><div class="ans">${escapeHtml(text)}</div></div>`;
}

function renderAnswer(data) {
  let html = "";
  if (data.answers.chatbot !== undefined) html += card("💬 Chatbot", data.answers.chatbot);
  if (data.answers.agent !== undefined)   html += card("🛠️ ReAct Agent", data.answers.agent);
  const m = data.metrics || {};
  html += `<div class="card"><h3>📊 Telemetry</h3><div class="metrics">
    <span>Model: <b>${escapeHtml(data.model)}</b></span>
    <span>LLM calls: <b>${m.calls||0}</b></span>
    <span>Tokens: <b>${m.total_tokens||0}</b></span>
    <span>LLM latency: <b>${m.latency_ms||0} ms</b></span>
    <span>Tổng thời gian: <b>${data.wall_ms||0} ms</b></span>
    <span>Cost ước tính: <b>$${(m.cost_estimate||0).toFixed(6)}</b></span>
  </div></div>`;
  $("out").innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

$("send").addEventListener("click", send);
$("q").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); });
</script>
</body>
</html>"""


def main():
    global _FORCED_PROVIDER
    parser = argparse.ArgumentParser(description="Web UI cho Trợ lý Lab Hệ nhúng")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--provider", default=None, help="local | openai | google")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    _FORCED_PROVIDER = args.provider
    print(f"* Mở trình duyệt tại http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
