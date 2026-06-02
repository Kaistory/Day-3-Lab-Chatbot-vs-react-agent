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
import uuid

# Vietnamese-safe stdout/stderr trên console Windows (cp1252) — phải chạy trước
# khi import các module tạo logging StreamHandler.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, make_response, render_template_string, request,
    stream_with_context,
)
from werkzeug.exceptions import HTTPException

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
    # HTTPException (404 thiếu route, 405 sai method...) giữ nguyên mã của nó,
    # không gộp thành 500 (tránh /favicon.ico báo 500 giả).
    if isinstance(e, HTTPException):
        return e
    logger.error(f"Lỗi web chưa bắt: {_short_error(e)}", exc_info=False)
    return jsonify({"error": "Đã có lỗi xảy ra phía máy chủ. Vui lòng thử lại."}), 500


@app.route("/favicon.ico")
def favicon():
    """Không có icon riêng -> trả 204 để trình duyệt thôi xin (đỡ rác log/console)."""
    return ("", 204)

# --- Lịch sử hội thoại theo phiên (để AI trả lời như 1 cuộc hội thoại) ---------
# Lưu phía SERVER theo session id (cookie), KHÔNG nhét vào Chatbot/Agent vì các
# engine được cache dùng chung cho mọi người dùng. In-memory: hợp cho 1 tiến trình
# (Flask threaded / 1 container). Giữ tối đa _MAX_TURNS lượt gần nhất.
_SESSIONS = {}                 # sid -> list[{"role": "user"|"assistant", "content": str}]
_SESSIONS_LOCK = threading.Lock()
_HISTORY_COOKIE = "lab_sid"
_MAX_TURNS = 12                # ~6 lượt hỏi-đáp gần nhất
_MAX_TURN_CHARS = 1500        # cắt mỗi lượt khi lưu, tránh prompt phình to


def _get_sid():
    """Lấy session id từ cookie, tạo mới nếu chưa có."""
    return request.cookies.get(_HISTORY_COOKIE) or uuid.uuid4().hex


def _get_history(sid):
    """Bản sao lịch sử của phiên (đọc an toàn dưới lock)."""
    with _SESSIONS_LOCK:
        return list(_SESSIONS.get(sid, []))


def _append_turns(sid, user_text, assistant_text):
    """Thêm lượt hỏi + đáp vào lịch sử phiên, cắt ngắn và giới hạn số lượt."""
    user_text = (user_text or "").strip()[:_MAX_TURN_CHARS]
    assistant_text = (assistant_text or "").strip()[:_MAX_TURN_CHARS]
    if not user_text and not assistant_text:
        return
    with _SESSIONS_LOCK:
        h = _SESSIONS.setdefault(sid, [])
        if user_text:
            h.append({"role": "user", "content": user_text})
        if assistant_text:
            h.append({"role": "assistant", "content": assistant_text})
        if len(h) > _MAX_TURNS:
            del h[: len(h) - _MAX_TURNS]


def _primary_answer(answers):
    """Câu trả lời để lưu vào lịch sử: ưu tiên agent, sau đó chatbot."""
    if not isinstance(answers, dict):
        return ""
    return answers.get("agent") or answers.get("chatbot") or ""


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
    # Đảm bảo trình duyệt có cookie session id ngay từ lần tải trang -> cả
    # /api/ask và /api/ask_stream (EventSource) đều gửi kèm cookie này.
    sid = _get_sid()
    resp = make_response(render_template_string(_HTML, tools=TOOLS))
    resp.set_cookie(
        _HISTORY_COOKIE, sid, max_age=7 * 24 * 3600,
        samesite="Lax", httponly=True,
    )
    return resp


@app.route("/api/reset", methods=["POST"])
def reset():
    """Xoá lịch sử hội thoại của phiên (gọi khi bấm 'Xoá')."""
    sid = _get_sid()
    with _SESSIONS_LOCK:
        _SESSIONS.pop(sid, None)
    return jsonify({"ok": True})


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

    sid = _get_sid()
    history = _get_history(sid)  # các lượt trước để trả lời theo ngữ cảnh
    started = time.time()
    start_index = len(tracker.session_metrics)
    agent_trace = []  # các lần gọi tool (tool, args, observation) để hiện trên UI
    try:
        if mode == "chatbot":
            result = {"chatbot": engines["chatbot"].ask(question, history=history)}
        elif mode == "compare":
            result = {
                "chatbot": engines["chatbot"].ask(question, history=history),
                "agent": engines["agent"].run(question, trace=agent_trace, history=history),
            }
        else:  # agent
            result = {"agent": engines["agent"].run(question, trace=agent_trace, history=history)}
    except Exception as e:
        # Lỗi đã được ghi vào file log; trả về trình duyệt thông điệp gọn, không dump.
        logger.error(f"Xử lý câu hỏi lỗi: {_short_error(e)}", exc_info=False)
        return jsonify({"error": f"Lỗi khi xử lý: {_short_error(e)}"}), 500

    _append_turns(sid, question, _primary_answer(result))
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
    # Đọc session id + lịch sử NGAY (còn trong request context, trước khi vào
    # generator chạy ngoài context).
    sid = _get_sid()
    history = _get_history(sid)

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
                    holder["answers"] = {"chatbot": engines["chatbot"].ask(question, history=history)}
                elif mode == "compare":
                    holder["answers"] = {
                        "chatbot": engines["chatbot"].ask(question, history=history),
                        "agent": engines["agent"].run(question, trace=trace, history=history),
                    }
                else:  # agent
                    holder["answers"] = {"agent": engines["agent"].run(question, trace=trace, history=history)}
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
            _append_turns(sid, question, _primary_answer(holder["answers"]))
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
_HTML = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trợ lý Lab Hệ nhúng (IT4210)</title>
<style>
  /* ===== Design tokens — dark mặc định, light qua [data-theme="light"] ===== */
  :root {
    --bg:#0b1020; --bg-soft:#0e1530; --card:#141c30; --card-2:#0d1424;
    --line:#283455; --txt:#e8edf7; --mut:#93a1bd;
    --acc:#5b9dff; --acc-2:#a78bfa; --grad:linear-gradient(135deg,#5b9dff,#a78bfa);
    --user-fg:#06122a; --code-bg:#0a1020;
    --danger-bg:#3b1d22; --danger-line:#7f1d1d; --danger-fg:#fca5a5;
    --glow1:rgba(91,157,255,.18); --glow2:rgba(167,139,250,.15);
    --shadow:0 10px 30px rgba(0,0,0,.35);
  }
  [data-theme="light"] {
    --bg:#f4f7fc; --bg-soft:#eef2f9; --card:#ffffff; --card-2:#f3f6fb;
    --line:#e2e8f2; --txt:#0f1729; --mut:#5b6678;
    --acc:#2563eb; --acc-2:#7c3aed; --grad:linear-gradient(135deg,#2563eb,#7c3aed);
    --user-fg:#ffffff; --code-bg:#eef2fb;
    --danger-bg:#fde8e8; --danger-line:#f5b5b5; --danger-fg:#b42318;
    --glow1:rgba(37,99,235,.10); --glow2:rgba(124,58,237,.08);
    --shadow:0 10px 30px rgba(15,23,42,.10);
  }
  * { box-sizing: border-box; }
  html, body { height:100%; }
  body { margin:0; font-family: system-ui, "Segoe UI", Roboto, sans-serif;
         background:var(--bg); color:var(--txt); display:flex; flex-direction:column;
         height:100vh; transition:background .25s, color .25s; }
  body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:
      radial-gradient(900px 520px at 85% -140px, var(--glow1), transparent 60%),
      radial-gradient(820px 520px at -10% 115%, var(--glow2), transparent 55%); }

  /* ===== Header (glass) ===== */
  header { padding:12px 18px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:12px; flex-wrap:wrap; flex:0 0 auto;
           background:color-mix(in srgb, var(--bg) 70%, transparent);
           backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); }
  .brand { display:flex; align-items:center; gap:9px; font-size:16px; font-weight:800;
           white-space:nowrap; margin:0; }
  .brand .logo { width:30px; height:30px; border-radius:9px; display:grid; place-items:center;
                 background:var(--grad); box-shadow:0 4px 14px var(--glow1); font-size:16px; }
  .brand .name { background:var(--grad); -webkit-background-clip:text; background-clip:text;
                 color:transparent; }
  header .ctrls { display:flex; gap:8px; margin-left:auto; flex-wrap:wrap; align-items:center; }
  select { font:inherit; border-radius:9px; border:1px solid var(--line);
           background:var(--card); color:var(--txt); padding:7px 10px; font-size:13px;
           cursor:pointer; transition:border-color .15s; }
  select:hover { border-color:var(--acc); }
  .ghost { background:var(--card); border:1px solid var(--line); color:var(--mut);
           border-radius:9px; padding:7px 11px; font:inherit; font-size:13px; cursor:pointer;
           transition:transform .12s, color .15s, border-color .15s; line-height:1; }
  .ghost:hover { color:var(--txt); border-color:var(--acc); transform:translateY(-1px); }

  /* ===== Khu hội thoại ===== */
  #chat { flex:1 1 auto; overflow-y:auto; padding:22px 0; scroll-behavior:smooth; }
  .wrap { max-width:840px; margin:0 auto; padding:0 18px; }
  .msg { display:flex; margin:16px 0; gap:11px; animation:rise .28s ease both; }
  @keyframes rise { from{opacity:0; transform:translateY(8px)} to{opacity:1; transform:none} }
  .msg.user { justify-content:flex-end; }
  .avatar { width:32px; height:32px; border-radius:50%; flex:0 0 auto; display:grid;
            place-items:center; font-size:16px; background:var(--card); border:1px solid var(--line); }
  .msg.bot .avatar { background:var(--grad); border:none; box-shadow:0 4px 12px var(--glow2); }
  .msg.user .avatar { order:2; }
  .bubble { position:relative; max-width:78%; padding:12px 15px; border-radius:16px;
            line-height:1.55; word-wrap:break-word; box-shadow:var(--shadow); }
  .msg.user .bubble { background:var(--grad); color:var(--user-fg); border-bottom-right-radius:5px;
                      font-weight:500; white-space:pre-wrap; }
  .msg.bot  .bubble { background:var(--card); border:1px solid var(--line);
                      border-bottom-left-radius:5px; }
  .bubble .tag { display:inline-block; font-size:10.5px; font-weight:800; letter-spacing:.5px;
                 text-transform:uppercase; margin-bottom:6px; padding:2px 8px; border-radius:999px;
                 background:color-mix(in srgb, var(--acc) 16%, transparent); color:var(--acc); }
  .bubble.err { background:var(--danger-bg); border-color:var(--danger-line); color:var(--danger-fg); }
  .copy { position:absolute; top:8px; right:8px; opacity:0; border:1px solid var(--line);
          background:var(--card-2); color:var(--mut); border-radius:7px; padding:3px 8px;
          font-size:11px; cursor:pointer; transition:opacity .15s, color .15s; }
  .msg.bot:hover .copy { opacity:1; }
  .copy:hover { color:var(--txt); border-color:var(--acc); }

  /* ===== Markdown trong câu trả lời ===== */
  .md { white-space:normal; }
  .md > :first-child { margin-top:0; } .md > :last-child { margin-bottom:0; }
  .md p { margin:.45em 0; } .md strong { font-weight:700; color:var(--txt); }
  .md em { font-style:italic; } .md a { color:var(--acc); }
  .md h3,.md h4 { margin:.6em 0 .3em; font-size:1.02em; }
  .md ul,.md ol { margin:.4em 0; padding-left:1.35em; } .md li { margin:.22em 0; }
  .md code { background:var(--code-bg); padding:1px 6px; border-radius:5px;
             font-family:"Cascadia Code",Consolas,monospace; font-size:.88em; }
  .md pre { background:var(--code-bg); border:1px solid var(--line); border-radius:9px;
            padding:10px 12px; overflow:auto; margin:.5em 0; }
  .md pre code { background:none; padding:0; }

  .metrics { display:flex; gap:7px; flex-wrap:wrap; margin-top:9px; padding-top:9px;
             border-top:1px dashed var(--line); }
  .metrics span { font-size:11px; color:var(--mut); background:var(--card-2);
                  border:1px solid var(--line); border-radius:7px; padding:3px 8px; }
  .metrics b { color:var(--txt); font-weight:600; }

  /* ===== Khối tool (Observation) ===== */
  .trace { margin-top:10px; border-top:1px dashed var(--line); padding-top:9px; }
  .trace > summary { cursor:pointer; color:var(--acc); font-size:12px; font-weight:700;
                     list-style:none; user-select:none; display:inline-flex; gap:6px; }
  .trace > summary::-webkit-details-marker { display:none; }
  .trace > summary::before { content:"▸"; transition:transform .15s; }
  .trace[open] > summary::before { transform:rotate(90deg); }
  .tstep { margin:9px 0 0; }
  .tcall { font-size:12.5px; color:var(--txt); margin-bottom:3px;
           font-family:"Cascadia Code",Consolas,monospace; }
  .tcall b { color:var(--acc-2); } .tcall i { color:var(--mut); font-style:normal; }
  .tnum { display:inline-block; min-width:24px; color:var(--mut); font-size:11px; }
  .tobs { margin:0; white-space:pre-wrap; word-wrap:break-word; font-size:12px; line-height:1.5;
          background:var(--code-bg); border:1px solid var(--line); border-radius:9px;
          padding:8px 10px; max-height:230px; overflow:auto; color:var(--txt); }

  /* ===== Empty state + chips gợi ý ===== */
  .empty { text-align:center; color:var(--mut); margin-top:9vh; font-size:14px; }
  .empty .hello { font-size:34px; margin-bottom:6px; }
  .empty h2 { margin:.2em 0; font-size:18px; color:var(--txt); }
  .empty code { background:var(--code-bg); padding:1px 6px; border-radius:5px; }
  .chips { display:flex; flex-wrap:wrap; gap:9px; justify-content:center; margin-top:18px; }
  .chip { border:1px solid var(--line); background:var(--card); color:var(--txt);
          padding:8px 13px; border-radius:999px; cursor:pointer; font:inherit; font-size:13px;
          transition:transform .12s, border-color .15s, background .15s; }
  .chip:hover { border-color:var(--acc); transform:translateY(-2px);
                background:color-mix(in srgb, var(--acc) 10%, var(--card)); }

  .dots span { display:inline-block; width:6px; height:6px; margin:0 1.5px; border-radius:50%;
               background:var(--acc); animation:b 1.2s infinite; }
  .dots span:nth-child(2){animation-delay:.2s} .dots span:nth-child(3){animation-delay:.4s}
  @keyframes b { 0%,80%,100%{opacity:.3; transform:translateY(0)} 40%{opacity:1; transform:translateY(-2px)} }

  /* ===== Composer ===== */
  footer { flex:0 0 auto; border-top:1px solid var(--line);
           background:color-mix(in srgb, var(--bg) 70%, transparent);
           backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); }
  .composer { max-width:840px; margin:0 auto; padding:13px 18px 6px; display:flex; gap:10px;
              align-items:flex-end; }
  .field { flex:1; display:flex; align-items:flex-end; background:var(--card);
           border:1px solid var(--line); border-radius:14px; padding:3px 6px;
           transition:border-color .15s, box-shadow .15s; }
  .field:focus-within { border-color:var(--acc); box-shadow:0 0 0 3px var(--glow1); }
  textarea { flex:1; font:inherit; border:none; outline:none; background:transparent;
             color:var(--txt); padding:9px 9px; resize:none; max-height:170px; line-height:1.5; }
  #send { background:var(--grad); color:#fff; border:none; font-weight:700; cursor:pointer;
          padding:0 20px; height:44px; border-radius:13px; font:inherit; font-weight:700;
          box-shadow:0 6px 18px var(--glow1); transition:transform .12s, box-shadow .15s, opacity .15s; }
  #send:hover:not(:disabled) { transform:translateY(-1px); box-shadow:0 9px 24px var(--glow1); }
  #send:disabled { opacity:.45; cursor:wait; box-shadow:none; }
  .hint { max-width:840px; margin:0 auto; padding:2px 18px 11px; color:var(--mut); font-size:11px;
          text-align:center; }
  .hint code { background:var(--code-bg); padding:1px 5px; border-radius:4px; }

  @media (max-width:560px){ .bubble{max-width:88%} .brand .name{display:none} }
</style>
</head>
<body>
<header>
  <h1 class="brand"><span class="logo">🤖</span><span class="name">Trợ lý Lab Hệ nhúng</span></h1>
  <div class="ctrls">
    <select id="mode" title="Chế độ">
      <option value="agent">🛠️ ReAct Agent</option>
      <option value="chatbot">💬 Chatbot</option>
      <option value="compare">⚖️ So sánh</option>
    </select>
    <select id="provider" title="Provider">
      <option value="">Provider mặc định</option>
      <option value="local">local (Phi-3)</option>
      <option value="openai">openai</option>
      <option value="google">google</option>
    </select>
    <button id="theme" class="ghost" title="Đổi giao diện sáng/tối" aria-label="Đổi giao diện sáng/tối">🌙</button>
    <a href="/logs" class="ghost" style="text-decoration:none" title="Xem log server">📜 Log</a>
    <button id="clear" class="ghost" title="Xoá hội thoại" aria-label="Xoá hội thoại">🗑 Xoá</button>
  </div>
</header>

<div id="chat">
  <div class="wrap" id="stream">
    <div class="empty" id="empty">
      <div class="hello">🤖</div>
      <h2>Trợ lý Lab Hệ nhúng (IT4210)</h2>
      Hỏi mình về <b>mục đích · chuẩn bị · sơ đồ chân · bài tập</b> của Lab 1/2/3.
      <div class="chips">
        <button class="chip" data-q="Lab 2 cần chuẩn bị phần cứng gì?">Lab 2 cần chuẩn bị gì?</button>
        <button class="chip" data-q="Sơ đồ chân ghép nối RC522 ở Lab 2 là gì?">Sơ đồ chân RC522?</button>
        <button class="chip" data-q="Mục đích của Lab 1 là gì?">Mục đích Lab 1?</button>
        <button class="chip" data-q="Hướng dẫn phần RFID của Lab 2">Hướng dẫn RFID Lab 2</button>
        <button class="chip" data-q="Chỗ nào trong tài liệu nói về ngắt ngoài?">Tìm 'ngắt' trong tài liệu</button>
      </div>
    </div>
  </div>
</div>

<footer>
  <div class="composer">
    <div class="field"><textarea id="q" rows="1" placeholder="Nhập câu hỏi…  (Enter để gửi · Shift+Enter xuống dòng)"></textarea></div>
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

// Markdown -> HTML AN TOÀN: escape TRƯỚC rồi mới định dạng, nên nội dung từ
// model/server không thể chèn thẻ HTML. Hỗ trợ: code block, `code`, **đậm**,
// *nghiêng*, tiêu đề #, danh sách -/1., link [..](http..).
function mdInline(t) {
  return t
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function mdToHtml(src) {
  let s = escapeHtml(String(src || ""));
  const blocks = [];
  s = s.replace(/```([\s\S]*?)```/g, (_, c) => {
    blocks.push(`<pre><code>${c.replace(/^\n/, "").replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  let html = "", list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of s.split("\n")) {
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList(); const lvl = Math.max(3, Math.min(4, m[1].length));
      html += `<h${lvl}>${mdInline(m[2])}</h${lvl}>`;
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${mdInline(m[1])}</li>`;
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${mdInline(m[1])}</li>`;
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList(); html += `<p>${mdInline(line)}</p>`;
    }
  }
  closeList();
  return html.replace(/<p>\u0000(\d+)\u0000<\/p>|\u0000(\d+)\u0000/g,
                      (_, a, b) => blocks[a != null ? a : b]);
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
  return `<span class="tag">${tag}</span><div class="md">${mdToHtml(text)}</div>`;
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
  let html = "", raw = "";
  if (a.chatbot !== undefined && a.agent !== undefined) {
    // chế độ so sánh: hai nhãn trong cùng một bong bóng
    html += answerHtml("💬 Chatbot", a.chatbot);
    html += `<hr style="border:none;border-top:1px solid var(--line);margin:13px 0">`;
    html += answerHtml("🛠️ ReAct Agent", a.agent);
    raw = `[Chatbot]\n${a.chatbot}\n\n[ReAct Agent]\n${a.agent}`;
  } else if (a.chatbot !== undefined) {
    html += answerHtml("💬 Chatbot", a.chatbot); raw = a.chatbot;
  } else if (a.agent !== undefined) {
    html += answerHtml("🛠️ ReAct Agent", a.agent); raw = a.agent;
  }
  html += traceHtml(data.trace);
  html += metricsHtml(data);
  bubble.innerHTML = `<button class="copy" title="Sao chép câu trả lời">📋 Copy</button>` + html;
  const btn = bubble.querySelector(".copy");
  if (btn && raw) btn.addEventListener("click", () => {
    (navigator.clipboard?.writeText(raw) || Promise.reject()).then(() => {
      btn.textContent = "✓ Đã chép";
      setTimeout(() => { btn.textContent = "📋 Copy"; }, 1500);
    }).catch(() => { btn.textContent = "✗ Lỗi"; });
  });
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

// Markup empty (kèm chips) dùng lại khi bấm "Xoá".
const EMPTY_HTML = `<div class="empty" id="empty">
  <div class="hello">🤖</div>
  <h2>Trợ lý Lab Hệ nhúng (IT4210)</h2>
  Hỏi mình về <b>mục đích · chuẩn bị · sơ đồ chân · bài tập</b> của Lab 1/2/3.
  <div class="chips">
    <button class="chip" data-q="Lab 2 cần chuẩn bị phần cứng gì?">Lab 2 cần chuẩn bị gì?</button>
    <button class="chip" data-q="Sơ đồ chân ghép nối RC522 ở Lab 2 là gì?">Sơ đồ chân RC522?</button>
    <button class="chip" data-q="Mục đích của Lab 1 là gì?">Mục đích Lab 1?</button>
    <button class="chip" data-q="Hướng dẫn phần RFID của Lab 2">Hướng dẫn RFID Lab 2</button>
    <button class="chip" data-q="Chỗ nào trong tài liệu nói về ngắt ngoài?">Tìm 'ngắt' trong tài liệu</button>
  </div>
</div>`;

// Chips: bấm là điền câu hỏi rồi gửi (event delegation -> chip sau khi Xoá vẫn chạy).
stream.addEventListener("click", e => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $("q").value = chip.dataset.q; autosize(); send();
});

// Toggle sáng/tối, lưu localStorage.
const THEME_KEY = "lab_theme";
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  $("theme").textContent = t === "light" ? "☀️" : "🌙";
}
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
$("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, next); applyTheme(next);
});

$("send").addEventListener("click", send);
$("q").addEventListener("input", autosize);
$("q").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("clear").addEventListener("click", () => {
  stream.innerHTML = EMPTY_HTML;
  $("q").focus();
  // Xoá luôn lịch sử hội thoại phía server cho phiên này.
  fetch("/api/reset", { method: "POST" }).catch(() => {});
});
$("q").focus();
</script>
</body>
</html>"""


# --- Trang xem log (đọc /api/logs) ---------------------------------------------
_LOGS_HTML = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log server — Trợ lý Lab Hệ nhúng</title>
<style>
  :root {
    --bg:#0b1020; --card:#141c30; --card-2:#0d1424; --line:#283455; --txt:#e8edf7;
    --mut:#93a1bd; --acc:#5b9dff; --acc-2:#a78bfa; --grad:linear-gradient(135deg,#5b9dff,#a78bfa);
    --code-bg:#0a1020; --glow:rgba(91,157,255,.16); --shadow:0 8px 24px rgba(0,0,0,.30);
  }
  [data-theme="light"] {
    --bg:#f4f7fc; --card:#ffffff; --card-2:#f3f6fb; --line:#e2e8f2; --txt:#0f1729;
    --mut:#5b6678; --acc:#2563eb; --acc-2:#7c3aed; --grad:linear-gradient(135deg,#2563eb,#7c3aed);
    --code-bg:#eef2fb; --glow:rgba(37,99,235,.10); --shadow:0 8px 24px rgba(15,23,42,.08);
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family: system-ui,"Segoe UI",Roboto,sans-serif; background:var(--bg);
         color:var(--txt); transition:background .25s,color .25s; }
  body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:radial-gradient(900px 500px at 85% -140px, var(--glow), transparent 60%); }
  header { padding:12px 18px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:10px; flex-wrap:wrap;
           background:color-mix(in srgb, var(--bg) 70%, transparent);
           backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
           position:sticky; top:0; z-index:5; }
  .brand { display:flex; align-items:center; gap:9px; margin:0; font-size:16px; font-weight:800; }
  .brand .logo { width:30px; height:30px; border-radius:9px; display:grid; place-items:center;
                 background:var(--grad); box-shadow:0 4px 14px var(--glow); }
  .brand .name { background:var(--grad); -webkit-background-clip:text; background-clip:text; color:transparent; }
  .spacer { margin-left:auto; }
  select, button, a.btn { font:inherit; font-size:13px; border-radius:9px; border:1px solid var(--line);
    background:var(--card); color:var(--txt); padding:7px 11px; cursor:pointer; text-decoration:none;
    transition:border-color .15s, transform .12s; }
  select:hover, button:hover, a.btn:hover { border-color:var(--acc); transform:translateY(-1px); }
  main { max-width:980px; margin:0 auto; padding:18px; }
  .summary { color:var(--mut); font-size:13px; margin-bottom:12px; }
  .entry { background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:11px 13px; margin-bottom:9px; box-shadow:var(--shadow); }
  .entry .top { display:flex; align-items:center; gap:10px; font-size:12px; color:var(--mut); }
  .badge { font-weight:800; font-size:10.5px; padding:3px 9px; border-radius:999px; letter-spacing:.3px;
           background:color-mix(in srgb, var(--acc) 14%, transparent); color:var(--acc); }
  .badge.LLM_METRIC { color:#84cc16; background:rgba(132,204,22,.14); }
  .badge.AGENT_END { color:#38bdf8; background:rgba(56,189,248,.14); }
  .badge.AGENT_STEP { color:#f59e0b; background:rgba(245,158,11,.14); }
  .badge.AGENT_START { color:#a78bfa; background:rgba(167,139,250,.16); }
  .badge.ERROR, .badge.RAW, .badge.AGENT_LLM_FAILED, .badge.AGENT_TIMEOUT { color:#f87171; background:rgba(248,113,113,.14); }
  .entry pre { white-space:pre-wrap; word-wrap:break-word; margin:8px 0 0; font-size:12.5px;
               line-height:1.5; color:var(--txt); background:var(--code-bg); border:1px solid var(--line);
               border-radius:9px; padding:9px 11px; max-height:320px; overflow:auto; }
  .kv { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; font-size:12px; }
  .kv span { color:var(--mut); background:var(--card-2); border:1px solid var(--line);
             border-radius:7px; padding:3px 8px; }
  .kv b { color:var(--txt); }
  .empty { color:var(--mut); text-align:center; margin-top:12vh; }
</style>
</head>
<body>
<header>
  <h1 class="brand"><span class="logo">📜</span><span class="name">Log server</span></h1>
  <select id="date" title="Ngày"></select>
  <select id="event" title="Loại sự kiện"><option value="">Tất cả sự kiện</option></select>
  <button id="reload">↻ Tải lại</button>
  <button id="theme" title="Đổi giao diện sáng/tối" aria-label="Đổi giao diện sáng/tối">🌙</button>
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

// Toggle sáng/tối (dùng chung khoá localStorage với trang chat).
const THEME_KEY = "lab_theme";
function applyTheme(t){ document.documentElement.dataset.theme = t; $("theme").textContent = t === "light" ? "☀️" : "🌙"; }
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
$("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, next); applyTheme(next);
});

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
