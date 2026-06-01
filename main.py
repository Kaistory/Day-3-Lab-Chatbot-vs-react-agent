"""
Entry point for the Embedded Systems (IT4210) Lab Assistant.

Usage:
    python main.py chatbot          # baseline chatbot (no tools)
    python main.py agent            # ReAct agent (with lab tools)
    python main.py compare "câu hỏi"   # run both on one question

    python main.py agent --provider local|openai|google
    python main.py agent --once "Lab 2 cần chuẩn bị gì?"

Provider defaults to DEFAULT_PROVIDER in .env (local Phi-3 if unset).
"""
import argparse
import sys

# Ensure Vietnamese prints/logs work on Windows consoles (cp1252) — must run
# before importing modules that create logging StreamHandlers.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv

from src.core.provider_factory import create_provider
from src.chatbot import Chatbot
from src.agent.agent import ReActAgent
from src.tools import TOOLS


def _banner(title: str):
    print("=" * 60)
    print(title)
    print("=" * 60)


def run_chatbot(provider_name, once):
    llm = create_provider(provider_name)
    bot = Chatbot(llm)
    _banner(f"CHATBOT BASELINE (provider={llm.model_name}) — gõ 'exit' để thoát")
    _loop(lambda q: bot.ask(q), once)


def run_agent(provider_name, once):
    llm = create_provider(provider_name)
    agent = ReActAgent(llm, TOOLS, max_steps=6)
    _banner(f"ReAct AGENT (provider={llm.model_name}) — {len(TOOLS)} tools — gõ 'exit' để thoát")
    _loop(lambda q: agent.run(q), once)


def run_compare(provider_name, question):
    llm = create_provider(provider_name)
    if not question:
        question = input("Câu hỏi để so sánh: ").strip()
    bot = Chatbot(llm)
    agent = ReActAgent(llm, TOOLS, max_steps=6)

    _banner("CHATBOT trả lời")
    print(bot.ask(question))
    _banner("ReAct AGENT trả lời")
    print(agent.run(question))


def _loop(answer_fn, once):
    if once:
        print(f"\n> {once}\n")
        print(answer_fn(once))
        return
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTạm biệt!")
            return
        if q.lower() in ("exit", "quit", "q", ""):
            print("Tạm biệt!")
            return
        print(answer_fn(q))


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Trợ lý Lab môn Hệ nhúng (IT4210)")
    parser.add_argument("mode", choices=["chatbot", "agent", "compare"], help="Chế độ chạy")
    parser.add_argument("question", nargs="?", default=None, help="Câu hỏi (cho mode compare)")
    parser.add_argument("--provider", default=None, help="openai | google | local")
    parser.add_argument("--once", default=None, help="Chạy 1 câu hỏi rồi thoát")
    args = parser.parse_args()

    try:
        if args.mode == "chatbot":
            run_chatbot(args.provider, args.once)
        elif args.mode == "agent":
            run_agent(args.provider, args.once)
        elif args.mode == "compare":
            run_compare(args.provider, args.question or args.once)
    except Exception as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
