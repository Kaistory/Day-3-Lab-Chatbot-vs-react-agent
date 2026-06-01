"""
Tests for the lab knowledge tools — run without any LLM / API key.

    pytest tests/test_tools.py -q
    python tests/test_tools.py        # also runs as a plain script
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from src.knowledge import loader
from src.tools import lab_tools, TOOLS, get_tool


def test_knowledge_loads():
    data = loader.load()
    assert set(data["labs"].keys()) == {"1", "2", "3"}


def test_get_lab_objective():
    out = lab_tools.get_lab_objective("2")
    assert "RFID" in out and "Mục đích" in out


def test_get_lab_preparation():
    out = lab_tools.get_lab_preparation("3")
    assert "TouchGFX" in out and "Phần cứng" in out


def test_list_available_labs():
    out = lab_tools.list_available_labs("")
    assert "Lab 1" in out and "Lab 2" in out and "Lab 3" in out


def test_get_lab_sections():
    out = lab_tools.get_lab_sections("2")
    assert "3.4" in out and "Thiết kế sơ đồ mạch" in out


def test_get_lab_exercises():
    out = lab_tools.get_lab_exercises("1")
    assert "DisplayLEDs" in out and "Bài tập" in out


def test_lookup_pin_mapping_component():
    out = lab_tools.lookup_pin_mapping("rc522")
    assert "PE4" in out and "PE2" in out


def test_search_diacritics_insensitive():
    # "hong ngoai" (no accents) should still find the IR / NEC content in Lab 1.
    out = lab_tools.search_lab_docs("hong ngoai")
    assert "Lab 1" in out


def test_registry_consistency():
    assert len(TOOLS) == 10
    assert get_tool("list_available_labs") is not None
    assert get_tool("get_lab_objective") is not None
    assert get_tool("get_lab_sections") is not None
    assert get_tool("get_lab_exercises") is not None
    assert get_tool("does_not_exist") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("\nDemo get_exercise_guide('2 rfid'):\n")
    print(lab_tools.get_exercise_guide("2 rfid"))
