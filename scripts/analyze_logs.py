"""
Aggregate reliability / performance analyzer for the Lab 3 telemetry logs.

Parses the structured JSON events written by `src/telemetry/logger.py` into
`logs/*.log` and produces an industry-style dashboard comparing the **Chatbot
baseline** against the **ReAct Agent**:

  * Latency P50 / P95 / P99 and average
  * Token usage (prompt / completion / total) and estimated cost
  * Loop count (Thought->Action->Observation steps) per agent task
  * Failure breakdown (LLM failure, loop-guard trip, timeout, parser fallbacks)
  * Aggregate success rate

Each LLM_METRIC is attributed to whichever mode (chatbot / agent) was most
recently started, so the same telemetry stream cleanly splits into two columns.

Usage:
    python scripts/analyze_logs.py                 # all logs, all providers
    python scripts/analyze_logs.py --exclude-mock  # drop mock test runs
    python scripts/analyze_logs.py --provider openai
    python scripts/analyze_logs.py --markdown      # emit Markdown tables
"""
import argparse
import glob
import json
import os
from typing import Any, Dict, List, Optional

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)


def _load_events(paths: List[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    # Non-JSON lines (plain logger.error output) are skipped.
                    continue
    return events


def _percentile(values: List[float], pct: float) -> float:
    """Nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def _blank_bucket() -> Dict[str, Any]:
    return {
        "requests": 0,        # number of LLM calls
        "tasks": 0,           # number of START events (user queries)
        "latencies": [],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "steps": [],          # loop count per finished agent task
        "failures": 0,        # llm failed / chatbot failed
        "loop_guard": 0,
        "timeouts": 0,
    }


def analyze(events: List[Dict[str, Any]],
            provider_filter: Optional[str],
            exclude_mock: bool) -> Dict[str, Dict[str, Any]]:
    buckets = {"chatbot": _blank_bucket(), "agent": _blank_bucket()}
    mode: Optional[str] = None  # current active mode for LLM_METRIC attribution

    for ev in events:
        etype = ev.get("event")
        data = ev.get("data", {})

        if etype == "CHATBOT_START":
            mode = "chatbot"
            buckets["chatbot"]["tasks"] += 1
        elif etype == "AGENT_START":
            mode = "agent"
            buckets["agent"]["tasks"] += 1
        elif etype == "LLM_METRIC" and mode:
            provider = data.get("provider", "unknown")
            if exclude_mock and provider == "mock":
                continue
            if provider_filter and provider != provider_filter:
                continue
            b = buckets[mode]
            b["requests"] += 1
            b["latencies"].append(data.get("latency_ms", 0))
            b["prompt_tokens"] += data.get("prompt_tokens", 0)
            b["completion_tokens"] += data.get("completion_tokens", 0)
            b["total_tokens"] += data.get("total_tokens", 0)
            b["cost"] += data.get("cost_estimate", 0.0)
        elif etype == "AGENT_END":
            steps = data.get("steps")
            if isinstance(steps, int):
                buckets["agent"]["steps"].append(steps)
        elif etype == "AGENT_LLM_FAILED":
            buckets["agent"]["failures"] += 1
        elif etype == "CHATBOT_FAILED":
            buckets["chatbot"]["failures"] += 1
        elif etype == "AGENT_LOOP_GUARD":
            buckets["agent"]["loop_guard"] += 1
        elif etype == "AGENT_TIMEOUT":
            buckets["agent"]["timeouts"] += 1

    return buckets


def analyze_by_model(events: List[Dict[str, Any]],
                     provider_filter: Optional[str],
                     exclude_mock: bool) -> Dict[str, Dict[str, Any]]:
    """Gom các LLM_METRIC theo model -> danh sách token để tính min/max/avg."""
    models: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if ev.get("event") != "LLM_METRIC":
            continue
        d = ev.get("data", {})
        provider = d.get("provider", "unknown")
        if exclude_mock and provider == "mock":
            continue
        if provider_filter and provider != provider_filter:
            continue
        model = d.get("model", "unknown")
        m = models.setdefault(model, {
            "provider": provider, "total": [], "prompt": [], "completion": []})
        m["total"].append(d.get("total_tokens", 0))
        m["prompt"].append(d.get("prompt_tokens", 0))
        m["completion"].append(d.get("completion_tokens", 0))
    return models


def _minmaxavg(vals: List[float]):
    """Trả (min, max, avg) cho 1 danh sách; (0,0,0.0) nếu rỗng."""
    if not vals:
        return 0, 0, 0.0
    return min(vals), max(vals), round(sum(vals) / len(vals), 1)


def render_by_model(models: Dict[str, Dict[str, Any]], markdown: bool) -> str:
    """Bảng token min/max/avg (total) theo từng model."""
    cols = ["Model", "Prov.", "N", "tok_min", "tok_max", "tok_avg",
            "in_avg", "out_avg"]
    lines = []
    if markdown:
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join("---" for _ in cols) + " |")
    else:
        lines.append("  ".join(f"{c:>9}" if c != "Model" else f"{c:<22}" for c in cols))
    for model in sorted(models):
        m = models[model]
        n = len(m["total"])
        tmin, tmax, tavg = _minmaxavg(m["total"])
        _, _, pavg = _minmaxavg(m["prompt"])
        _, _, cavg = _minmaxavg(m["completion"])
        cells = [model, m["provider"], n, tmin, tmax, tavg, pavg, cavg]
        if markdown:
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        else:
            lines.append("  ".join(
                f"{str(c):<22}" if i == 0 else f"{str(c):>9}"
                for i, c in enumerate(cells)))
    return "\n".join(lines)


def _summarize(b: Dict[str, Any]) -> Dict[str, Any]:
    lat = b["latencies"]
    tasks = b["tasks"] or 1
    failed = b["failures"] + b["timeouts"]
    return {
        "tasks": b["tasks"],
        "requests": b["requests"],
        "avg_latency": round(sum(lat) / len(lat), 1) if lat else 0.0,
        "p50": _percentile(lat, 50),
        "p95": _percentile(lat, 95),
        "p99": _percentile(lat, 99),
        "total_tokens": b["total_tokens"],
        "avg_tokens_per_task": round(b["total_tokens"] / tasks, 1),
        "cost": round(b["cost"], 6),
        "avg_steps": round(sum(b["steps"]) / len(b["steps"]), 2) if b["steps"] else 0.0,
        "max_steps": max(b["steps"]) if b["steps"] else 0,
        "loop_guard": b["loop_guard"],
        "timeouts": b["timeouts"],
        "failures": b["failures"],
        "success_rate": round(100.0 * (b["tasks"] - failed) / tasks, 1),
    }


_ROWS = [
    ("Tasks (user queries)", "tasks", ""),
    ("LLM requests", "requests", ""),
    ("Avg latency", "avg_latency", "ms"),
    ("Latency P50", "p50", "ms"),
    ("Latency P95", "p95", "ms"),
    ("Latency P99", "p99", "ms"),
    ("Total tokens", "total_tokens", ""),
    ("Avg tokens / task", "avg_tokens_per_task", ""),
    ("Est. cost (USD)", "cost", "$"),
    ("Avg loop count", "avg_steps", ""),
    ("Max loop count", "max_steps", ""),
    ("Loop-guard trips", "loop_guard", ""),
    ("Timeouts", "timeouts", ""),
    ("Hard failures", "failures", ""),
    ("Success rate", "success_rate", "%"),
]


def _fmt(val: Any, unit: str) -> str:
    if unit == "$":
        return f"${val}"
    if unit:
        return f"{val} {unit}"
    return str(val)


def render(summary_cb: Dict[str, Any], summary_ag: Dict[str, Any],
           markdown: bool) -> str:
    sep = " | " if markdown else "  "
    edge = "| " if markdown else ""
    end = " |" if markdown else ""
    lines = []
    header = f"{edge}{'Metric':<22}{sep}{'Chatbot':>14}{sep}{'ReAct Agent':>14}{end}"
    lines.append(header)
    if markdown:
        lines.append(f"| {'-'*22} | {'-'*14}:| {'-'*14}:|")
    else:
        lines.append("-" * len(header))
    for label, key, unit in _ROWS:
        cb = _fmt(summary_cb[key], unit)
        ag = _fmt(summary_ag[key], unit)
        lines.append(f"{edge}{label:<22}{sep}{cb:>14}{sep}{ag:>14}{end}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze Lab 3 telemetry logs.")
    ap.add_argument("--provider", help="Only count this provider (openai/google/local/mock).")
    ap.add_argument("--exclude-mock", action="store_true", help="Drop mock test runs.")
    ap.add_argument("--markdown", action="store_true", help="Emit Markdown tables.")
    ap.add_argument("--by-model", action="store_true",
                    help="Also show per-model token min/max/avg.")
    ap.add_argument("--logs", default=os.path.join(_LOG_DIR, "*.log"),
                    help="Glob for log files (default: logs/*.log).")
    args = ap.parse_args()

    paths = sorted(glob.glob(args.logs))
    if not paths:
        print(f"No log files matched: {args.logs}")
        return

    events = _load_events(paths)
    buckets = analyze(events, args.provider, args.exclude_mock)
    summary_cb = _summarize(buckets["chatbot"])
    summary_ag = _summarize(buckets["agent"])

    scope = []
    if args.provider:
        scope.append(f"provider={args.provider}")
    if args.exclude_mock:
        scope.append("excluding mock")
    scope_str = f" ({', '.join(scope)})" if scope else ""

    print(f"# Telemetry Dashboard{scope_str}")
    print(f"# Source: {len(paths)} file(s), {len(events)} events\n")
    print(render(summary_cb, summary_ag, args.markdown))

    if args.by_model:
        models = analyze_by_model(events, args.provider, args.exclude_mock)
        print(f"\n# Per-model token stats (min / max / avg over {sum(len(m['total']) for m in models.values())} requests)")
        print(render_by_model(models, args.markdown))


if __name__ == "__main__":
    main()
