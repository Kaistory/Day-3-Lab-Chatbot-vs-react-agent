"""
Chatbot baseline — a plain LLM with NO tools and NO reasoning loop.

This is the contrast point for the ReAct agent: it can only answer from the
model's own knowledge. For domain-specific lab questions (chân cắm chính xác,
mục đích từng bài) it will guess or hallucinate, motivating the agent approach.
"""
from typing import Optional

from src.core.llm_provider import LLMProvider
from src.telemetry.logger import logger
from src.telemetry.metrics import tracker

SYSTEM_PROMPT = (
    "Bạn là trợ lý hỗ trợ sinh viên môn Hệ nhúng (IT4210) tại HUST. "
    "Trả lời ngắn gọn, rõ ràng bằng tiếng Việt về mục đích lab, chuẩn bị lab "
    "và hướng dẫn bài tập. Nếu không chắc chắn, hãy nói rõ là không chắc."
)


class Chatbot:
    """Single-turn (or simple multi-turn) chatbot with no tool access."""

    def __init__(self, llm: LLMProvider, system_prompt: str = SYSTEM_PROMPT):
        self.llm = llm
        self.system_prompt = system_prompt

    def ask(self, user_input: str) -> str:
        logger.log_event("CHATBOT_START", {"input": user_input, "model": self.llm.model_name})
        try:
            result = self.llm.generate(user_input, system_prompt=self.system_prompt)
        except Exception as e:
            # Lỗi provider (vd 429 quota): log gọn ra file, trả thông điệp thân thiện.
            short = str(e).splitlines()[0][:200] if str(e).strip() else type(e).__name__
            logger.error(f"Chatbot LLM lỗi: {short}", exc_info=False)
            logger.log_event("CHATBOT_FAILED", {"error": short})
            return ("Xin lỗi, hệ thống AI tạm thời không phản hồi (có thể do mất mạng "
                    "hoặc dịch vụ quá tải). Vui lòng thử lại sau ít phút.")
        tracker.track_request(
            provider=result.get("provider", "unknown"),
            model=self.llm.model_name,
            usage=result.get("usage", {}),
            latency_ms=result.get("latency_ms", 0),
        )
        logger.log_event("CHATBOT_END", {"latency_ms": result.get("latency_ms", 0)})
        return result["content"]
