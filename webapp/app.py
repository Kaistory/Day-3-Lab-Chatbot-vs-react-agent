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
import glob
import json
import os
import sys
import threading
import time

# Vietnamese-safe stdout/stderr trên console Windows (cp1252) — phải chạy trước
# khi import các module tạo logging StreamHandler.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context

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
            # max_steps=10: đủ chỗ cho câu hỏi nhiều tool (vd 7 tool) gọi hết rồi
            # vẫn còn bước để viết Final Answer (tránh fallback về Observation cuối).
            "agent": ReActAgent(llm, TOOLS, max_steps=10),
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
    agent_trace = []  # các lần gọi tool (tool, args, observation) để hiện trên UI
    try:
        if mode == "chatbot":
            result = {"chatbot": engines["chatbot"].ask(question)}
        elif mode == "compare":
            result = {
                "chatbot": engines["chatbot"].ask(question),
                "agent": engines["agent"].run(question, trace=agent_trace),
            }
        else:  # agent
            result = {"agent": engines["agent"].run(question, trace=agent_trace)}
    except Exception as e:
        # Lỗi đã được ghi vào file log; trả về trình duyệt thông điệp gọn, không dump.
        logger.error(f"Xử lý câu hỏi lỗi: {_short_error(e)}", exc_info=False)
        return jsonify({"error": f"Lỗi khi xử lý: {_short_error(e)}"}), 500

    return jsonify({
        "mode": mode,
        "model": engines["model"],
        "answers": result,
        "trace": agent_trace,
        "metrics": _metrics_since(start_index),
        "wall_ms": int((time.time() - started) * 1000),
    })


@app.route("/api/ask_stream")
def ask_stream():
    """
    Streaming (SSE): chạy agent trong một thread và phát từng bước gọi tool NGAY
    KHI nó chạy xong, để web hiện tuần tự các tool từ đầu đến khi ra kết quả.

    Sự kiện SSE:
      start  -> {mode}
      step   -> {step, tool, args, observation}   (mỗi lần gọi tool)
      done   -> {model, answers, trace, metrics, wall_ms}
      failed -> {error}
    """
    question = (request.args.get("question") or "").strip()
    mode = (request.args.get("mode") or "agent").strip()
    provider = (request.args.get("provider") or "").strip() or None

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @stream_with_context
    def gen():
        if not question:
            yield sse("failed", {"error": "Vui lòng nhập câu hỏi."})
            return
        try:
            engines = _get_engines(provider)
        except Exception as e:
            logger.error(f"Init provider lỗi: {_short_error(e)}", exc_info=False)
            yield sse("failed", {"error": f"Không khởi tạo được provider: {_short_error(e)}"})
            return

        trace = []   # agent append từng bước tool vào đây trong lúc chạy (ở thread khác)
        holder = {"answers": None, "error": None, "done": False}
        start_index = len(tracker.session_metrics)
        started = time.time()

        def worker():
            try:
                if mode == "chatbot":
                    holder["answers"] = {"chatbot": engines["chatbot"].ask(question)}
                elif mode == "compare":
                    holder["answers"] = {
                        "chatbot": engines["chatbot"].ask(question),
                        "agent": engines["agent"].run(question, trace=trace),
                    }
                else:  # agent
                    holder["answers"] = {"agent": engines["agent"].run(question, trace=trace)}
            except Exception as e:
                logger.error(f"Stream xử lý lỗi: {_short_error(e)}", exc_info=False)
                holder["error"] = _short_error(e)
            finally:
                holder["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        yield sse("start", {"mode": mode})

        # Polling: phát các bước mới xuất hiện trong trace cho tới khi worker xong.
        sent = 0
        while True:
            while sent < len(trace):
                yield sse("step", trace[sent])
                sent += 1
            if holder["done"]:
                while sent < len(trace):   # flush nốt nếu trace vừa tăng
                    yield sse("step", trace[sent])
                    sent += 1
                break
            time.sleep(0.15)

        if holder["error"]:
            yield sse("failed", {"error": f"Lỗi khi xử lý: {holder['error']}"})
        else:
            yield sse("done", {
                "mode": mode,
                "model": engines["model"],
                "answers": holder["answers"] or {},
                "trace": trace,
                "metrics": _metrics_since(start_index),
                "wall_ms": int((time.time() - started) * 1000),
            })

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Xem log đã ghi trên server (logs/*.log) -----------------------------------
_LOG_DIR = "logs"


def _list_log_dates():
    """Danh sách ngày có log (theo tên file logs/YYYY-MM-DD.log), mới nhất trước."""
    files = glob.glob(os.path.join(_LOG_DIR, "*.log"))
    dates = [os.path.splitext(os.path.basename(f))[0] for f in files]
    return sorted(dates, reverse=True)


def _read_log(date, limit=500):
    """Đọc & parse file log của 1 ngày -> list bản ghi (mới nhất trước, cắt theo limit)."""
    path = os.path.join(_LOG_DIR, f"{date}.log")
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                # Dòng không phải JSON (vd log lỗi dạng text thuần) -> bọc lại để vẫn xem được.
                entries.append({"timestamp": "", "event": "RAW", "data": {"line": line}})
    entries.reverse()  # mới nhất trước
    return entries[:limit]


@app.route("/api/logs")
def api_logs():
    dates = _list_log_dates()
    date = (request.args.get("date") or (dates[0] if dates else "")).strip()
    # Chỉ chấp nhận ngày nằm trong danh sách file thật -> tránh path traversal.
    if date and date not in dates:
        return jsonify({"error": "Không có log cho ngày này.", "dates": dates}), 404
    event = (request.args.get("event") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit", 500)), 1), 2000)
    except (TypeError, ValueError):
        limit = 500

    entries = _read_log(date, limit=limit) if date else []
    events = sorted({e.get("event", "") for e in entries})
    if event:
        entries = [e for e in entries if e.get("event") == event]
    return jsonify({
        "date": date,
        "dates": dates,
        "events": events,
        "count": len(entries),
        "entries": entries,
    })


@app.route("/logs")
def logs_page():
    return render_template_string(_LOGS_HTML)


# --- Giao diện (1 trang HTML tĩnh, không cần asset ngoài) ----------------------
_HTML = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trợ lý Lab Hệ nhúng (IT4210)</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --line:#334155; --txt:#e2e8f0; --mut:#94a3b8;
          --acc:#38bdf8; --user:#0ea5e9; --bot:#1e293b; }
  * { box-sizing: border-box; }
  html, body { height:100%; }
  body { margin:0; font-family: system-ui, "Segoe UI", sans-serif; background:var(--bg);
         color:var(--txt); display:flex; flex-direction:column; height:100vh; }

  /* Thanh tiêu đề + điều khiển */
  header { padding:12px 18px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:14px; flex-wrap:wrap; flex:0 0 auto; }
  header h1 { margin:0; font-size:16px; white-space:nowrap; }
  header .ctrls { display:flex; gap:8px; margin-left:auto; flex-wrap:wrap; }
  select { font:inherit; border-radius:8px; border:1px solid var(--line);
           background:var(--card); color:var(--txt); padding:7px 10px; font-size:13px; }
  .ghost { background:transparent; border:1px solid var(--line); color:var(--mut);
           border-radius:8px; padding:7px 12px; font:inherit; font-size:13px; cursor:pointer; }
  .ghost:hover { color:var(--txt); }

  /* Khu vực hội thoại cuộn */
  #chat { flex:1 1 auto; overflow-y:auto; padding:20px 0; }
  .wrap { max-width:820px; margin:0 auto; padding:0 18px; }
  .msg { display:flex; margin:14px 0; gap:10px; }
  .msg.user { justify-content:flex-end; }
  .avatar { width:30px; height:30px; border-radius:50%; flex:0 0 auto; display:grid;
            place-items:center; font-size:16px; background:var(--card); border:1px solid var(--line); }
  .msg.user .avatar { order:2; }
  .bubble { max-width:76%; padding:11px 14px; border-radius:14px; line-height:1.55;
            white-space:pre-wrap; word-wrap:break-word; }
  .msg.user .bubble { background:var(--user); color:#04293a; border-bottom-right-radius:4px; }
  .msg.bot  .bubble { background:var(--bot); border:1px solid var(--line); border-bottom-left-radius:4px; }
  .bubble .tag { display:block; font-size:11px; font-weight:700; color:var(--acc);
                 margin-bottom:5px; text-transform:uppercase; letter-spacing:.4px; }
  .bubble.err { background:#3b1d22; border-color:#7f1d1d; color:#fca5a5; }
  .metrics { display:flex; gap:14px; flex-wrap:wrap; color:var(--mut); font-size:11px;
             margin-top:7px; padding-top:7px; border-top:1px dashed var(--line); }
  .metrics b { color:var(--txt); font-weight:600; }
  /* Khối hiển thị các lần gọi công cụ (tool I/O) */
  .trace { margin-top:9px; border-top:1px dashed var(--line); padding-top:8px; }
  .trace > summary { cursor:pointer; color:var(--acc); font-size:12px; font-weight:600;
                     list-style:none; user-select:none; }
  .trace > summary::-webkit-details-marker { display:none; }
  .trace > summary:hover { text-decoration:underline; }
  .tstep { margin:8px 0 0; }
  .tcall { font-size:12.5px; color:var(--txt); margin-bottom:3px; }
  .tcall b { color:#a3e635; } .tcall i { color:var(--mut); font-style:normal; }
  .tnum { display:inline-block; min-width:22px; color:var(--mut); font-size:11px; }
  .tobs { margin:0; white-space:pre-wrap; word-wrap:break-word; font-size:12px;
          line-height:1.5; background:#0b1220; border:1px solid var(--line);
          border-radius:8px; padding:7px 9px; max-height:220px; overflow:auto; color:var(--txt); }
  .empty { text-align:center; color:var(--mut); margin-top:12vh; font-size:14px; }
  .empty code { background:#0b1220; padding:1px 5px; border-radius:4px; }
  .dots span { display:inline-block; width:6px; height:6px; margin:0 1px; border-radius:50%;
               background:var(--mut); animation:b 1.2s infinite; }
  .dots span:nth-child(2){animation-delay:.2s} .dots span:nth-child(3){animation-delay:.4s}
  @keyframes b { 0%,80%,100%{opacity:.3} 40%{opacity:1} }

  /* Ô nhập dính đáy */
  footer { flex:0 0 auto; border-top:1px solid var(--line); background:var(--bg); }
  .composer { max-width:820px; margin:0 auto; padding:12px 18px; display:flex; gap:10px; align-items:flex-end; }
  textarea { flex:1; font:inherit; border-radius:12px; border:1px solid var(--line);
             background:var(--card); color:var(--txt); padding:11px 13px; resize:none;
             max-height:160px; line-height:1.5; }
  #send { background:var(--acc); color:#04293a; border:none; font-weight:700; cursor:pointer;
          padding:0 20px; height:44px; border-radius:12px; font:inherit; font-weight:700; }
  #send:disabled { opacity:.5; cursor:wait; }
  .hint { max-width:820px; margin:0 auto; padding:0 18px 10px; color:var(--mut); font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>🤖 Trợ lý Lab Hệ nhúng</h1>
  <div class="ctrls">
    <select id="mode" title="Chế độ">
      <option value="agent">ReAct Agent (có tools)</option>
      <option value="chatbot">Chatbot (không tools)</option>
      <option value="compare">So sánh cả hai</option>
    </select>
    <select id="provider" title="Provider">
      <option value="">Provider mặc định</option>
      <option value="local">local (Phi-3)</option>
      <option value="openai">openai</option>
      <option value="google">google</option>
    </select>
    <a href="/logs" class="ghost" style="text-decoration:none" title="Xem log server">📜 Log</a>
    <button id="clear" class="ghost" title="Xoá hội thoại">🗑 Xoá</button>
  </div>
</header>

<div id="chat">
  <div class="wrap" id="stream">
    <div class="empty" id="empty">
      👋 Hỏi mình về <b>Lab 1/2/3</b> môn Hệ nhúng (IT4210).<br>
      Ví dụ: <code>Lab 2 cần chuẩn bị phần cứng gì?</code> · <code>Sơ đồ chân RC522?</code>
    </div>
  </div>
</div>

<footer>
  <div class="composer">
    <textarea id="q" rows="1" placeholder="Nhập câu hỏi… (Enter để gửi, Shift+Enter xuống dòng)"></textarea>
    <button id="send">Gửi</button>
  </div>
  <div class="hint">
    {{ tools|length }} công cụ của agent: {% for t in tools %}<code>{{ t.name }}</code>{% if not loop.last %} · {% endif %}{% endfor %}
  </div>
</footer>

<script>
const $ = (id) => document.getElementById(id);
const stream = $("stream");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function scrollBottom() { $("chat").scrollTop = $("chat").scrollHeight; }

function addUser(text) {
  const e = $("empty"); if (e) e.remove();
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="avatar">🧑</div><div class="bubble">${escapeHtml(text)}</div>`;
  stream.appendChild(el); scrollBottom();
}

// Trả về phần tử .bubble để cập nhật nội dung sau (placeholder "đang gõ").
function addBot() {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `<div class="avatar">🤖</div><div class="bubble">` +
    `<span class="dots"><span></span><span></span><span></span></span></div>`;
  stream.appendChild(el); scrollBottom();
  return el.querySelector(".bubble");
}

function answerHtml(tag, text) {
  return `<span class="tag">${tag}</span>${escapeHtml(text)}`;
}

function metricsHtml(data) {
  const m = data.metrics || {};
  return `<div class="metrics">
    <span>Model: <b>${escapeHtml(data.model||"")}</b></span>
    <span>Calls: <b>${m.calls||0}</b></span>
    <span>Tokens: <b>${m.total_tokens||0}</b></span>
    <span>LLM: <b>${m.latency_ms||0} ms</b></span>
    <span>Tổng: <b>${data.wall_ms||0} ms</b></span>
    <span>Cost: <b>$${(m.cost_estimate||0).toFixed(6)}</b></span>
  </div>`;
}

// Khối các lần gọi công cụ: tên tool, tham số, và Observation (tool trả về).
function traceHtml(trace) {
  if (!trace || !trace.length) return "";
  const rows = trace.map(s => (
    `<div class="tstep">` +
      `<div class="tcall"><span class="tnum">#${s.step}</span>` +
        `<b>${escapeHtml(s.tool)}</b>(<i>${escapeHtml(s.args)}</i>)</div>` +
      `<pre class="tobs">${escapeHtml(s.observation)}</pre>` +
    `</div>`
  )).join("");
  return `<details class="trace"><summary>🛠️ ${trace.length} lần gọi công cụ — xem tool trả về</summary>${rows}</details>`;
}

function fillAnswer(bubble, data) {
  const a = data.answers || {};
  let html = "";
  if (a.chatbot !== undefined && a.agent !== undefined) {
    // chế độ so sánh: hai nhãn trong cùng một bong bóng
    html += answerHtml("💬 Chatbot", a.chatbot);
    html += `<hr style="border:none;border-top:1px solid var(--line);margin:12px 0">`;
    html += answerHtml("🛠️ ReAct Agent", a.agent);
  } else if (a.chatbot !== undefined) {
    html += answerHtml("💬 Chatbot", a.chatbot);
  } else if (a.agent !== undefined) {
    html += answerHtml("🛠️ ReAct Agent", a.agent);
  }
  html += traceHtml(data.trace);
  html += metricsHtml(data);
  bubble.innerHTML = html;
  scrollBottom();
}

// KHÔNG giới hạn thời gian chờ bot trả lời (mọi provider, kể cả local Phi-3 chậm).
const TIMEOUT_MS = null;
const MAX_RETRIES = 2;     // chỉ gửi lại khi LỖI MẠNG thật, không phải do hết giờ

const dotsHtml = '<span class="dots"><span></span><span></span><span></span></span>';

function fetchWithTimeout(url, opts, ms) {
  // ms rỗng (null/0) -> KHÔNG đặt giới hạn thời gian (vd local Phi-3 chạy CPU).
  if (!ms) return fetch(url, opts);
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...opts, signal: ctrl.signal })
    .finally(() => clearTimeout(t));
}

// Render các bước tool đang chạy (live) — mỗi tool 1 ô, kèm dots khi chưa xong.
function liveStepsHtml(tag, steps, running) {
  const rows = steps.map(s => (
    `<div class="tstep"><div class="tcall"><span class="tnum">#${s.step}</span>` +
      `<b>${escapeHtml(s.tool)}</b>(<i>${escapeHtml(s.args)}</i>)</div>` +
      `<pre class="tobs">${escapeHtml(s.observation)}</pre></div>`
  )).join("");
  const body = rows || `<span style="color:var(--mut);font-size:12px">Đang suy luận…</span>`;
  return `<span class="tag">${tag}</span>${body}${running ? " " + dotsHtml : ""}`;
}

// Gửi câu hỏi và STREAM từng bước tool (SSE) để hiện tuần tự từ đầu đến kết quả.
function send() {
  const ta = $("q");
  const question = ta.value.trim();
  if (!question) return;
  const mode = $("mode").value, provider = $("provider").value;

  addUser(question);
  ta.value = ""; autosize();
  $("send").disabled = true;
  const bubble = addBot();

  const steps = [];
  let finished = false;
  const tag = mode === "chatbot" ? "💬 Chatbot" : "🛠️ ReAct Agent (đang chạy)";
  const reset = () => { $("send").disabled = false; ta.focus(); };

  const params = new URLSearchParams({ question, mode, provider });
  const es = new EventSource("/api/ask_stream?" + params.toString());

  es.addEventListener("start", () => {
    bubble.classList.remove("err");
    bubble.innerHTML = liveStepsHtml(tag, steps, true);
    scrollBottom();
  });

  es.addEventListener("step", (ev) => {
    try { steps.push(JSON.parse(ev.data)); } catch (e) {}
    bubble.innerHTML = liveStepsHtml(tag, steps, true);
    scrollBottom();
  });

  es.addEventListener("done", (ev) => {
    finished = true; es.close();
    let data = {}; try { data = JSON.parse(ev.data); } catch (e) {}
    data.trace = steps;
    bubble.classList.remove("err");
    fillAnswer(bubble, data);
    reset();
  });

  es.addEventListener("failed", (ev) => {
    finished = true; es.close();
    let msg = "Lỗi không xác định";
    try { msg = JSON.parse(ev.data).error || msg; } catch (e) {}
    bubble.classList.add("err");
    bubble.innerHTML = "⚠️ " + escapeHtml(msg);
    scrollBottom(); reset();
  });

  // Lỗi KẾT NỐI SSE thật (không phải sự kiện 'failed') -> fallback gọi /api/ask.
  es.onerror = () => {
    if (finished) return;   // server đã đóng stream sau 'done' -> bỏ qua
    finished = true; es.close();
    bubble.innerHTML = `<span style="color:var(--mut);font-size:12px">` +
      `↻ Mất kết nối stream, thử lại (không stream)…</span> ` + dotsHtml;
    scrollBottom();
    sendFallback(question, mode, provider, bubble, reset);
  };
}

// Dự phòng: gọi /api/ask thường (không stream) khi SSE lỗi kết nối.
async function sendFallback(question, mode, provider, bubble, reset) {
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ question, mode, provider })
    });
    const data = await res.json();
    if (!res.ok) { bubble.classList.add("err"); bubble.innerHTML = "⚠️ " + escapeHtml(data.error || "Lỗi"); }
    else { bubble.classList.remove("err"); fillAnswer(bubble, data); }
  } catch (e) {
    bubble.classList.add("err");
    bubble.innerHTML = "⚠️ " + escapeHtml(String(e));
  } finally { scrollBottom(); reset(); }
}

function autosize() {
  const ta = $("q");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

$("send").addEventListener("click", send);
$("q").addEventListener("input", autosize);
$("q").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("clear").addEventListener("click", () => {
  stream.innerHTML = `<div class="empty" id="empty">👋 Hỏi mình về <b>Lab 1/2/3</b> môn Hệ nhúng (IT4210).</div>`;
  $("q").focus();
});
$("q").focus();
</script>
</body>
</html>"""


# --- Trang xem log (đọc /api/logs) ---------------------------------------------
_LOGS_HTML = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log server — Trợ lý Lab Hệ nhúng</title>
<style>
  :root { --bg:#0f172a; --card:#1e293b; --line:#334155; --txt:#e2e8f0; --mut:#94a3b8; --acc:#38bdf8; }
  * { box-sizing:border-box; }
  body { margin:0; font-family: system-ui,"Segoe UI",sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:12px 18px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:16px; }
  .spacer { margin-left:auto; }
  select, button, a.btn { font:inherit; font-size:13px; border-radius:8px; border:1px solid var(--line);
    background:var(--card); color:var(--txt); padding:7px 10px; cursor:pointer; text-decoration:none; }
  button:hover, a.btn:hover { color:#fff; }
  main { max-width:980px; margin:0 auto; padding:18px; }
  .summary { color:var(--mut); font-size:13px; margin-bottom:12px; }
  .entry { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:10px 12px; margin-bottom:8px; }
  .entry .top { display:flex; align-items:center; gap:10px; font-size:12px; color:var(--mut); }
  .badge { font-weight:700; font-size:11px; padding:2px 8px; border-radius:20px;
           border:1px solid var(--line); color:var(--acc); letter-spacing:.3px; }
  .badge.LLM_METRIC { color:#a3e635; } .badge.AGENT_END { color:#38bdf8; }
  .badge.AGENT_STEP { color:#fbbf24; } .badge.AGENT_START { color:#c084fc; }
  .badge.ERROR, .badge.RAW { color:#f87171; }
  .entry pre { white-space:pre-wrap; word-wrap:break-word; margin:8px 0 0; font-size:12.5px;
               line-height:1.5; color:var(--txt); max-height:320px; overflow:auto; }
  .kv { display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:var(--mut); }
  .kv b { color:var(--txt); }
  .empty { color:var(--mut); text-align:center; margin-top:12vh; }
</style>
</head>
<body>
<header>
  <h1>📜 Log server</h1>
  <select id="date" title="Ngày"></select>
  <select id="event" title="Loại sự kiện"><option value="">Tất cả sự kiện</option></select>
  <button id="reload">↻ Tải lại</button>
  <a class="btn spacer" href="/">← Về chat</a>
</header>
<main>
  <div class="summary" id="summary"></div>
  <div id="list"></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function metricRow(d){
  return `<div class="kv">
    <span>Model: <b>${esc(d.model||"")}</b></span>
    <span>Provider: <b>${esc(d.provider||"")}</b></span>
    <span>Tokens: <b>${d.total_tokens||0}</b> (in ${d.prompt_tokens||0} / out ${d.completion_tokens||0})</span>
    <span>Latency: <b>${d.latency_ms||0} ms</b></span>
    <span>Cost: <b>$${(d.cost_estimate||0).toFixed ? (d.cost_estimate||0).toFixed(6) : d.cost_estimate}</b></span>
  </div>`;
}

function entryHtml(e){
  const ev = e.event || "?";
  const t = (e.timestamp||"").replace("T"," ").slice(0,19);
  let body;
  if (ev === "LLM_METRIC" && e.data) {
    body = metricRow(e.data);
  } else if (e.data && typeof e.data === "object" && ("line" in e.data) && Object.keys(e.data).length===1) {
    body = `<pre>${esc(e.data.line)}</pre>`;
  } else {
    body = `<pre>${esc(JSON.stringify(e.data, null, 2))}</pre>`;
  }
  return `<div class="entry"><div class="top">
    <span class="badge ${esc(ev)}">${esc(ev)}</span><span>${esc(t)}</span>
  </div>${body}</div>`;
}

async function load(){
  const date = $("date").value, event = $("event").value;
  $("list").innerHTML = `<div class="empty">Đang tải…</div>`;
  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  if (event) qs.set("event", event);
  const res = await fetch("/api/logs?" + qs.toString());
  const data = await res.json();
  if (!res.ok){ $("list").innerHTML = `<div class="empty">${esc(data.error||"Lỗi tải log")}</div>`; return; }

  // Cập nhật dropdown ngày (1 lần, giữ lựa chọn hiện tại).
  if (!$("date").dataset.init && data.dates){
    $("date").innerHTML = data.dates.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
    if (data.date) $("date").value = data.date;
    $("date").dataset.init = "1";
  }
  // Cập nhật dropdown loại sự kiện theo các event có trong ngày.
  const cur = $("event").value;
  $("event").innerHTML = `<option value="">Tất cả sự kiện</option>` +
    (data.events||[]).filter(Boolean).map(ev => `<option value="${esc(ev)}">${esc(ev)}</option>`).join("");
  $("event").value = cur;

  $("summary").textContent = `Ngày ${data.date || "—"} · ${data.count} bản ghi (mới nhất trước, tối đa 500).`;
  $("list").innerHTML = (data.entries||[]).map(entryHtml).join("") || `<div class="empty">Không có bản ghi.</div>`;
}

$("date").addEventListener("change", () => { $("event").value=""; load(); });
$("event").addEventListener("change", load);
$("reload").addEventListener("click", load);
load();
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
