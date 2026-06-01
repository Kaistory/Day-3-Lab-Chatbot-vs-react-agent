"""
Test the ReAct loop logic with a scripted mock LLM (no API key / model needed).

    python tests/test_agent_loop.py
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
from src.tools import TOOLS


class MockLLM(LLMProvider):
    """Returns a pre-scripted sequence of completions, one per generate() call."""

    def __init__(self, scripted):
        super().__init__(model_name="mock")
        self._scripted = list(scripted)
        self._i = 0

    def generate(self, prompt, system_prompt=None):
        text = self._scripted[self._i] if self._i < len(self._scripted) else "Final Answer: (hết kịch bản)"
        self._i += 1
        return {
            "content": text,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "latency_ms": 1,
            "provider": "mock",
        }

    def stream(self, prompt, system_prompt=None):
        yield self.generate(prompt, system_prompt)["content"]


def test_tool_then_final():
    llm = MockLLM([
        "Thought: Cần tra chuẩn bị Lab 2.\nAction: get_lab_preparation(2)",
        "Thought: Đã có thông tin.\nFinal Answer: Lab 2 cần kit STM32F429I, Tiny RTC, OLED SH1106, RC522.",
    ])
    agent = ReActAgent(llm, TOOLS, max_steps=5)
    answer = agent.run("Lab 2 cần chuẩn bị gì?")
    assert "RC522" in answer
    print("PASS test_tool_then_final ->", answer)


def test_unknown_tool_recovers():
    llm = MockLLM([
        "Thought: thử.\nAction: nonexistent_tool(x)",
        "Final Answer: Xin lỗi, tôi đã chọn sai công cụ.",
    ])
    agent = ReActAgent(llm, TOOLS, max_steps=5)
    answer = agent.run("test")
    assert "xin lỗi" in answer.lower()
    print("PASS test_unknown_tool_recovers ->", answer)


def test_max_steps_guard():
    # Always asks for an action, never finalizes -> must stop at max_steps.
    llm = MockLLM(["Action: search_lab_docs(led)"] * 10)
    agent = ReActAgent(llm, TOOLS, max_steps=3)
    answer = agent.run("loop forever?")
    assert "max_steps" in answer or "giới hạn" in answer
    print("PASS test_max_steps_guard ->", answer[:60])


if __name__ == "__main__":
    test_tool_then_final()
    test_unknown_tool_recovers()
    test_max_steps_guard()
    print("\nTất cả test agent loop PASS.")
