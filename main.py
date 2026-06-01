import os
from dotenv import load_dotenv

from chatbot import Chatbot
from src.agent.agent import ReActAgent
from src.core.gemini_provider import GeminiProvider
from src.core.local_provider import LocalProvider
from src.core.openai_provider import OpenAIProvider
from src.tools import TOOLS


def build_provider():
    provider = os.getenv("DEFAULT_PROVIDER", "openai").lower()
    model = os.getenv("DEFAULT_MODEL", "gpt-4o")

    if provider == "openai":
        return OpenAIProvider(model_name=model, api_key=os.getenv("OPENAI_API_KEY"))
    if provider in {"google", "gemini"}:
        return GeminiProvider(model_name=model, api_key=os.getenv("GEMINI_API_KEY"))
    if provider == "local":
        return LocalProvider(model_path=os.getenv("LOCAL_MODEL_PATH", "./models/Phi-3-mini-4k-instruct-q4.gguf"))

    raise ValueError(f"Unsupported DEFAULT_PROVIDER: {provider}")


def main():
    load_dotenv()
    llm = build_provider()
    question = (
        "I want to buy 2 iPhones using coupon code WINNER and ship to Hanoi. "
        "Check stock and calculate the final total."
    )

    print("\n=== Baseline Chatbot ===")
    print(Chatbot(llm).run(question))

    print("\n=== ReAct Agent ===")
    agent = ReActAgent(llm=llm, tools=TOOLS, max_steps=6)
    print(agent.run(question))


if __name__ == "__main__":
    main()
