"""
Tool registry for the ReAct agent.

Each tool is a dict with: name, description (the LLM only knows the tool through
this string!), and func (callable taking one string arg, returning a string).
"""
from src.tools import lab_tools, web_tools

TOOLS = [
    {
        "name": "get_lab_objective",
        "description": "Lấy mục đích/mục tiêu của một bài lab Hệ nhúng. Tham số: số bài (1, 2 hoặc 3).",
        "func": lab_tools.get_lab_objective,
    },
    {
        "name": "get_lab_preparation",
        "description": "Lấy phần chuẩn bị (phần cứng, phần mềm, tài liệu) của một lab. Tham số: số bài (1, 2 hoặc 3).",
        "func": lab_tools.get_lab_preparation,
    },
    {
        "name": "get_exercise_guide",
        "description": "Lấy hướng dẫn các phần và bài tập của một lab. Tham số: số bài, có thể kèm chủ đề, vd '2 rfid'.",
        "func": lab_tools.get_exercise_guide,
    },
    {
        "name": "search_lab_docs",
        "description": "Tìm kiếm theo từ khóa trong toàn bộ tài liệu 3 lab (không phân biệt dấu). Tham số: từ khóa.",
        "func": lab_tools.search_lab_docs,
    },
    {
        "name": "lookup_pin_mapping",
        "description": "Tra cứu sơ đồ chân/ghép nối của một lab hoặc linh kiện (rc522, led, hs0038, ds1307...). Tham số: số bài hoặc tên linh kiện.",
        "func": lab_tools.lookup_pin_mapping,
    },
    {
        "name": "web_search",
        "description": "Tìm kiếm trên Internet khi câu hỏi nằm ngoài tài liệu lab (vd datasheet, chuẩn giao tiếp). Tham số: câu truy vấn.",
        "func": web_tools.web_search,
    },
    {
        "name": "fetch_url",
        "description": "Tải nội dung văn bản của một URL. Tham số: địa chỉ URL.",
        "func": web_tools.fetch_url,
    },
]


def get_tool(name: str):
    """Return the tool dict matching name, or None."""
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None
