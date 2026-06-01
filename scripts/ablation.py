"""
Ablation experiments for the ReAct agent's reliability guardrails.

Runs the SAME pathological inputs through the agent with individual safeguards
turned on/off, using a scripted mock LLM (no API key needed), and reports how
each guardrail changes wasted work and termination quality.

    python scripts/ablation.py

Experiments
-----------
1. Loop-guard (max_repeated_actions): a weak model that keeps emitting the same
   Action and never concludes. Measures redundant TOOL executions (= wasted
   work / cost / side effects) with the guard off vs on.
2. max_steps cap: the same non-terminating model. Measures how the step cap
   bounds total LLM calls (= bounded billing) and whether the agent still
   returns grounded data via the last-observation fallback.
3. Termination quality: a well-behaved model that concludes on step 3. Shows a
   too-tight max_steps starves a solvable task, while an adequate cap succeeds.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from src.core.llm_provider import LLMProvider
from src.agent.agent import ReActAgent
from src.telemetry.logger import logger

# Keep the console clean: file logging still records everything in logs/.
logger.silence_console()


class MockLLM(LLMProvider):
    """Returns scripted completions and counts how many times it was called."""

    def __init__(self, scripted):
        super().__init__(model_name="mock")
        self._scripted = list(scripted)
        self.calls = 0

    def generate(self, prompt, system_prompt=None):
        text = self._scripted[self.calls] if self.calls < len(self._scripted) \
            else self._scripted[-1]  # repeat last line forever
        self.calls += 1
        return {
            "content": text,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "latency_ms": 1,
            "provider": "mock",
        }

    def stream(self, prompt, system_prompt=None):
        yield self.generate(prompt, system_prompt)["content"]


def _counting_tool():
    """A fake search tool that records how many times it actually executed."""
    state = {"execs": 0}

    def func(args):
        state["execs"] += 1
        return "Lab 1: dùng chân PA0 cho nút nhấn, PG13/PG14 cho LED."

    tool = {"name": "search_lab_docs",
            "description": "Tìm trong tài liệu lab.", "func": func}
    return tool, state


def run_case(scripted, *, max_steps, max_repeated_actions):
    tool, state = _counting_tool()
    llm = MockLLM(scripted)
    agent = ReActAgent(
        llm, [tool],
        max_steps=max_steps,
        max_repeated_actions=max_repeated_actions,
    )
    answer = agent.run("Lab 1 dùng chân nào cho LED?")
    grounded = "PG13" in answer or "PA0" in answer
    return {
        "llm_calls": llm.calls,
        "tool_execs": state["execs"],
        "grounded": grounded,
        "answer": answer,
    }


def _row(label, r):
    g = "yes" if r["grounded"] else "NO"
    return f"| {label:<34} | {r['llm_calls']:>9} | {r['tool_execs']:>10} | {g:>8} |"


def main():
    print("# Ablation: ReAct reliability guardrails\n")

    # ---- Experiment 1 & 2: non-terminating model (worst case) ----
    loop_forever = ["Thought: tra cứu.\nAction: search_lab_docs(led)"]
    print("## Exp 1+2 — non-terminating model (always repeats one Action)\n")
    print("| Config                             | LLM calls | Tool execs | Grounded |")
    print("| ---------------------------------- | --------: | ---------: | -------: |")
    base = run_case(loop_forever, max_steps=6, max_repeated_actions=999)
    print(_row("baseline (no guard, max_steps=6)", base))
    guard = run_case(loop_forever, max_steps=6, max_repeated_actions=2)
    print(_row("+ loop-guard (repeat>2)", guard))
    tight = run_case(loop_forever, max_steps=3, max_repeated_actions=2)
    print(_row("+ loop-guard + max_steps=3", tight))

    saved_execs = base["tool_execs"] - guard["tool_execs"]
    saved_calls = base["llm_calls"] - tight["llm_calls"]
    print(f"\n  -> loop-guard cut redundant tool executions by "
          f"{saved_execs} ({base['tool_execs']} -> {guard['tool_execs']}).")
    print(f"  -> tighter max_steps cut LLM calls by "
          f"{saved_calls} ({base['llm_calls']} -> {tight['llm_calls']}).")
    print(f"  -> all configs still returned GROUNDED data via last-observation "
          f"fallback: base={base['grounded']}, guard={guard['grounded']}, "
          f"tight={tight['grounded']}.")

    # ---- Experiment 3: termination quality vs max_steps ----
    well_behaved = [
        "Thought: cần tra cứu.\nAction: search_lab_docs(led)",
        "Thought: xem thêm.\nAction: search_lab_docs(led)",
        "Thought: đủ rồi.\nFinal Answer: Lab 1 dùng PG13/PG14 cho LED, PA0 cho nút.",
    ]
    print("\n## Exp 3 — well-behaved model (concludes on step 3)\n")
    print("| Config                             | LLM calls | Tool execs | Grounded |")
    print("| ---------------------------------- | --------: | ---------: | -------: |")
    starved = run_case(well_behaved, max_steps=2, max_repeated_actions=2)
    print(_row("max_steps=2 (too tight)", starved))
    ok = run_case(well_behaved, max_steps=5, max_repeated_actions=2)
    print(_row("max_steps=5 (adequate)", ok))
    print(f"\n  -> too-tight cap stopped before the model's Final Answer but the "
          f"fallback still surfaced grounded data (grounded={starved['grounded']}); "
          f"adequate cap let it conclude cleanly in {ok['llm_calls']} calls.")


if __name__ == "__main__":
    main()
