"""
Demo: MỘT câu hỏi gộp kích hoạt CẢ 7 tool của ReAct agent.

Câu hỏi được thiết kế 7 ý, mỗi ý map đúng 1 tool:
  1) mục đích Lab 2          -> get_lab_objective
  2) chuẩn bị Lab 2          -> get_lab_preparation
  3) hướng dẫn phần RFID     -> get_exercise_guide
  4) chỗ nào nói về 'ngắt'   -> search_lab_docs
  5) sơ đồ chân RC522        -> lookup_pin_mapping
  6) datasheet DS1307 online -> web_search
  7) tải trang datasheet đó  -> fetch_url

Chạy:
    python scripts/demo_all_tools.py                 # provider mặc định (.env)
    python scripts/demo_all_tools.py --provider openai
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv
from src.core.provider_factory import create_provider
from src.agent.agent import ReActAgent
from src.tools import TOOLS
from src.telemetry.logger import logger

load_dotenv()
logger.silence_console()

# Một câu hỏi duy nhất, 7 ý đánh số để "ép" agent đi qua đủ 7 tool.
QUESTION = (
    "Mình cần một bản tổng hợp ĐẦY ĐỦ về Lab 2 môn Hệ nhúng, làm lần lượt 7 việc, "
    "mỗi việc dùng đúng một công cụ:\n"
    "1) Nêu MỤC ĐÍCH của Lab 2.\n"
    "2) Liệt kê phần CHUẨN BỊ (phần cứng/phần mềm/tài liệu) của Lab 2.\n"
    "3) Lấy HƯỚNG DẪN phần RFID (RC522) của Lab 2.\n"
    "4) TÌM trong tài liệu 3 lab những chỗ nhắc tới 'ngắt'.\n"
    "5) Tra SƠ ĐỒ CHÂN ghép nối của RC522.\n"
    "6) TÌM TRÊN MẠNG datasheet DS1307 để biết địa chỉ I2C.\n"
    "7) TẢI nội dung một trang datasheet DS1307 tìm được để trích địa chỉ I2C.\n"
    "Làm tuần tự, mỗi bước một Action, rồi tổng hợp Final Answer."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None, help="openai | google | local")
    ap.add_argument("--max-steps", type=int, default=10)
    args = ap.parse_args()

    llm = create_provider(args.provider)
    agent = ReActAgent(llm, TOOLS, max_steps=args.max_steps)

    print(f"# Provider: {llm.model_name} · max_steps={args.max_steps}\n")
    print("# Câu hỏi:\n" + QUESTION + "\n")

    trace = []
    answer = agent.run(QUESTION, trace=trace)

    print("# Trace — các tool agent đã gọi:")
    fired = []
    for s in trace:
        fired.append(s["tool"])
        obs = s["observation"].replace("\n", " ")
        print(f"  #{s['step']:<2} {s['tool']}({s['args']}) -> {obs[:80]}")

    all_names = [t["name"] for t in TOOLS]
    used = set(fired)
    missing = [n for n in all_names if n not in used]
    print(f"\n# Đã gọi {len(used)}/7 tool: {sorted(used)}")
    if missing:
        print(f"# CHƯA gọi: {missing}")
    else:
        print("# ✅ Đủ cả 7 tool!")

    print("\n# Final Answer:\n" + answer)


if __name__ == "__main__":
    main()
