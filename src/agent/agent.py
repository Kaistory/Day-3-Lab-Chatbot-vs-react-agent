import re
import time
from typing import List, Dict, Any, Optional

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

    Production-grade safeguards (xem 4 trụ cột production-grade):
      * Reliability / Error Handling: lời gọi LLM được bọc try/except và tự động
        thử lại với backoff lũy thừa; LLM lỗi không làm sập vòng lặp.
      * Safety: giới hạn độ dài input người dùng và tham số tool để chống lạm dụng
        / prompt quá khổ làm tràn context.
      * Reliability: loop-guard phát hiện agent gọi lặp đúng một Action để thoát
        sớm thay vì đốt hết max_steps một cách vô ích.
    """

    # Matches:  Action: get_lab_preparation(2)
    _ACTION_RE = re.compile(r"Action:\s*([A-Za-z_][\w]*)\s*\((.*?)\)", re.DOTALL)
    _FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)

    def __init__(
        self,
        llm: LLMProvider,
        tools: List[Dict[str, Any]],
        max_steps: int = 5,
        max_input_chars: int = 4000,
        max_arg_chars: int = 500,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        max_repeated_actions: int = 2,
    ):
        """
        Args:
            max_steps: số bước ReAct tối đa.
            max_input_chars: cắt input người dùng dài quá ngưỡng (Safety).
            max_arg_chars: cắt tham số tool dài quá ngưỡng (Safety).
            max_retries: số lần thử lại khi LLM lỗi (Reliability).
            retry_backoff: giây chờ cơ sở, nhân đôi sau mỗi lần thử (exponential backoff).
            max_repeated_actions: số lần một Action giống hệt được lặp trước khi dừng.
        """
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.max_input_chars = max_input_chars
        self.max_arg_chars = max_arg_chars
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.max_repeated_actions = max_repeated_actions

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

    def _sanitize_input(self, user_input: str) -> str:
        """Safety guardrail: chuẩn hóa và cắt input người dùng quá dài."""
        text = (user_input or "").strip()
        if len(text) > self.max_input_chars:
            logger.log_event(
                "AGENT_INPUT_TRUNCATED",
                {"original_len": len(text), "max": self.max_input_chars},
            )
            text = text[: self.max_input_chars]
        return text

    def _generate_with_retry(self, transcript: str, system_prompt: str) -> Optional[Dict[str, Any]]:
        """
        Reliability guardrail: gọi LLM với retry + exponential backoff.
        Trả về dict kết quả, hoặc None nếu mọi lần thử đều thất bại.
        """
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.llm.generate(transcript, system_prompt=system_prompt)
            except Exception as e:  # mất mạng, API bên thứ 3 lỗi/timeout, rate limit...
                last_error = e
                logger.error(
                    f"LLM lỗi (lần {attempt + 1}/{self.max_retries + 1}): {e}",
                    exc_info=False,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (2 ** attempt))
        logger.log_event("AGENT_LLM_FAILED", {"error": str(last_error)})
        return None

    def run(self, user_input: str) -> str:
        """Run the ReAct loop and return the final answer text."""
        user_input = self._sanitize_input(user_input)
        if not user_input:
            return "Vui lòng nhập câu hỏi."

        logger.log_event("AGENT_START", {"input": user_input, "model": self.llm.model_name})

        system_prompt = self.get_system_prompt()
        transcript = f"Question: {user_input}\n"
        steps = 0
        final_answer = None
        action_counts: Dict[str, int] = {}  # loop-guard: đếm Action lặp lại

        while steps < self.max_steps:
            steps += 1
            result = self._generate_with_retry(transcript, system_prompt=system_prompt)

            # Reliability: LLM không phản hồi sau khi đã retry -> báo lỗi thân thiện,
            # không để ngoại lệ làm sập tiến trình.
            if result is None:
                final_answer = (
                    "Xin lỗi, hệ thống AI tạm thời không phản hồi (có thể do mất mạng "
                    "hoặc dịch vụ quá tải). Vui lòng thử lại sau ít phút."
                )
                logger.log_event("AGENT_END", {"steps": steps, "answer": final_answer, "status": "llm_failed"})
                return final_answer

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

                # Safety: cắt tham số tool quá dài.
                if len(tool_args) > self.max_arg_chars:
                    tool_args = tool_args[: self.max_arg_chars]

                # Reliability loop-guard: nếu agent gọi đúng (tool, args) lặp lại
                # quá ngưỡng, dừng và nhắc nó kết luận thay vì lặp vô ích.
                key = f"{tool_name}({tool_args})"
                action_counts[key] = action_counts.get(key, 0) + 1
                if action_counts[key] > self.max_repeated_actions:
                    logger.log_event("AGENT_LOOP_GUARD", {"step": steps, "action": key})
                    transcript += (
                        "Observation: Bạn đã gọi lặp lại cùng một Action. "
                        "Hãy đưa ra Final Answer dựa trên các Observation đã có.\n"
                    )
                    continue

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
