import re
from typing import List, Dict, Any

from src.core.llm_provider import LLMProvider
from src.telemetry.logger import logger
from src.telemetry.metrics import tracker


class ReActAgent:
    """
    A ReAct-style agent that follows the Thought -> Action -> Observation loop.

    Tools are dicts: {name, description, func}, where func(args: str) -> str.
    The agent drives an LLM to emit `Action: tool_name(args)` lines, executes the
    matching tool, feeds the Observation back, and repeats until `Final Answer:`
    or max_steps is reached.
    """

    # Matches:  Action: get_lab_preparation(2)
    _ACTION_RE = re.compile(r"Action:\s*([A-Za-z_][\w]*)\s*\((.*?)\)", re.DOTALL)
    _FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)

    def __init__(self, llm: LLMProvider, tools: List[Dict[str, Any]], max_steps: int = 5):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.history: List[str] = []

    def get_system_prompt(self) -> str:
        """Instruct the LLM to follow the ReAct format and list available tools."""
        tool_descriptions = "\n".join(
            f"- {t['name']}: {t['description']}" for t in self.tools
        )
        tool_names = ", ".join(t["name"] for t in self.tools)
        return f"""Bạn là trợ lý hỗ trợ sinh viên môn Hệ nhúng (IT4210) tại HUST.
Bạn giúp trả lời về MỤC ĐÍCH LAB, CHUẨN BỊ LAB và HƯỚNG DẪN BÀI TẬP.

Bạn có các công cụ sau:
{tool_descriptions}

Hãy suy luận theo định dạng ReAct, MỖI LƯỢT chỉ xuất MỘT khối:
Thought: suy nghĩ của bạn về việc cần làm tiếp theo.
Action: tên_công_cụ(tham số)

Sau mỗi Action, hệ thống sẽ trả về một dòng:
Observation: kết quả của công cụ.

Lặp lại Thought/Action/Observation nếu cần. Khi đã đủ thông tin, kết thúc bằng:
Final Answer: câu trả lời cuối cùng bằng tiếng Việt cho người dùng.

QUY TẮC:
- Chỉ dùng các công cụ có tên trong: {tool_names}.
- Tham số công cụ đặt trong ngoặc đơn, không thêm dấu nháy thừa.
- Sau khi viết một dòng Action, DỪNG LẠI và chờ Observation, không tự bịa Observation.
- Nếu câu hỏi đơn giản và đã rõ, có thể trả lời ngay bằng Final Answer."""

    def run(self, user_input: str) -> str:
        """Run the ReAct loop and return the final answer text."""
        logger.log_event("AGENT_START", {"input": user_input, "model": self.llm.model_name})

        system_prompt = self.get_system_prompt()
        transcript = f"Question: {user_input}\n"
        steps = 0
        final_answer = None

        while steps < self.max_steps:
            steps += 1
            result = self.llm.generate(transcript, system_prompt=system_prompt)
            tracker.track_request(
                provider=result.get("provider", "unknown"),
                model=self.llm.model_name,
                usage=result.get("usage", {}),
                latency_ms=result.get("latency_ms", 0),
            )
            text = result["content"].strip()

            # Keep only up to the first Observation the model might have hallucinated.
            text = re.split(r"\nObservation:", text)[0].strip()
            logger.log_event("AGENT_STEP", {"step": steps, "llm_output": text})
            transcript += text + "\n"

            # 1) Did the model give a Final Answer?
            final_match = self._FINAL_RE.search(text)
            if final_match:
                final_answer = final_match.group(1).strip()
                break

            # 2) Did the model request an Action?
            action_match = self._ACTION_RE.search(text)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_args = action_match.group(2).strip().strip("'\"")
                observation = self._execute_tool(tool_name, tool_args)
                logger.log_event(
                    "AGENT_OBSERVATION",
                    {"step": steps, "tool": tool_name, "args": tool_args, "observation": observation},
                )
                transcript += f"Observation: {observation}\n"
                continue

            # 3) Neither -> treat the whole output as the answer (graceful fallback).
            final_answer = text
            break

        if final_answer is None:
            final_answer = (
                "Đã đạt giới hạn số bước (max_steps) mà chưa có Final Answer. "
                "Hãy thử hỏi cụ thể hơn."
            )
            logger.log_event("AGENT_TIMEOUT", {"steps": steps})

        logger.log_event("AGENT_END", {"steps": steps, "answer": final_answer})
        return final_answer

    def _execute_tool(self, tool_name: str, args: str) -> str:
        """Look up and run a tool by name; return its string Observation."""
        for tool in self.tools:
            if tool["name"] == tool_name:
                try:
                    return str(tool["func"](args))
                except Exception as e:  # never let a tool crash the loop
                    logger.error(f"Tool '{tool_name}' lỗi: {e}")
                    return f"Lỗi khi chạy công cụ {tool_name}: {e}"
        available = ", ".join(t["name"] for t in self.tools)
        return f"Không có công cụ tên '{tool_name}'. Các công cụ hợp lệ: {available}."
