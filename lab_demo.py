import ast
import operator
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from src.core.gemini_provider import GeminiProvider
from src.core.local_provider import LocalProvider
from src.core.openai_provider import OpenAIProvider
from src.telemetry.logger import logger
from src.telemetry.metrics import tracker


PRODUCTS = {
    "iphone": {"price": 999.0, "stock": 5, "weight_kg": 0.25},
    "airpods": {"price": 179.0, "stock": 12, "weight_kg": 0.08},
    "macbook": {"price": 1499.0, "stock": 3, "weight_kg": 1.4},
}

COUPONS = {
    "WINNER": 10,
    "STUDENT": 15,
    "FREESHIP": 0,
}

DESTINATION_BASE_SHIPPING = {
    "hanoi": 5.0,
    "ho chi minh": 6.0,
    "danang": 7.0,
}

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def normalize(value: str) -> str:
    return str(value).strip().lower()


def eval_node(node):
    if isinstance(node, ast.Expression):
        return eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](eval_node(node.left), eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](eval_node(node.operand))
    raise ValueError("Only basic arithmetic expressions are allowed.")


def calculate(expression: str) -> str:
    try:
        parsed = ast.parse(str(expression), mode="eval")
        result = eval_node(parsed)
        return str(round(result, 4) if isinstance(result, float) else result)
    except Exception as exc:
        return f"CALCULATION_ERROR: {exc}"


def get_price(item_name: str) -> str:
    item = PRODUCTS.get(normalize(item_name))
    if not item:
        return f"PRODUCT_NOT_FOUND: {item_name}"
    return str(item["price"])


def check_stock(item_name: str) -> str:
    item = PRODUCTS.get(normalize(item_name))
    if not item:
        return f"PRODUCT_NOT_FOUND: {item_name}"
    return str(item["stock"])


def get_discount(coupon_code: str) -> str:
    return str(COUPONS.get(str(coupon_code).strip().upper(), 0))


def calc_shipping(input_text: str) -> str:
    try:
        weight_text, destination = str(input_text).split(",", 1)
        weight_kg = float(weight_text.strip())
        base = DESTINATION_BASE_SHIPPING.get(normalize(destination), 10.0)
        return str(round(base + weight_kg * 2.0, 2))
    except Exception:
        return "SHIPPING_ERROR: expected input format 'weight_kg, destination'"


TOOLS = [
    {
        "name": "calculate",
        "description": "Evaluate a basic arithmetic expression. Example: 999 * 2 * 0.9 + 5.5",
        "function": calculate,
    },
    {
        "name": "get_price",
        "description": "Return unit price in USD for a product name. Example: iphone",
        "function": get_price,
    },
    {
        "name": "check_stock",
        "description": "Return available stock quantity for a product name. Example: iphone",
        "function": check_stock,
    },
    {
        "name": "get_discount",
        "description": "Return discount percent for a coupon code. Example: WINNER",
        "function": get_discount,
    },
    {
        "name": "calc_shipping",
        "description": "Calculate shipping cost. Format: weight_kg, destination. Example: 0.5, Hanoi",
        "function": calc_shipping,
    },
]


class Chatbot:
    def __init__(self, llm):
        self.llm = llm

    def run(self, user_input: str) -> str:
        logger.log_event("CHATBOT_START", {"input": user_input, "model": self.llm.model_name})
        result = self.llm.generate(user_input)
        tracker.track_request(
            provider=result.get("provider", "unknown"),
            model=self.llm.model_name,
            usage=result.get("usage", {}),
            latency_ms=result.get("latency_ms", 0),
        )
        logger.log_event("CHATBOT_END", {"latency_ms": result.get("latency_ms", 0)})
        return result.get("content", "")


class ReActAgent:
    def __init__(self, llm, tools: List[Dict[str, Any]], max_steps: int = 6):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    def get_system_prompt(self) -> str:
        tool_descriptions = "\n".join([f"- {tool['name']}: {tool['description']}" for tool in self.tools])
        return f"""
You are a ReAct agent. Use tools for facts, calculation, and multi-step tasks.

Available tools:
{tool_descriptions}

Use this exact format:
Thought: one short reasoning step.
Action: tool_name(arguments)

After Observation, continue with another Thought/Action or:
Final Answer: your final response.

Do not invent tool names. Do not write Observation yourself.
"""

    def run(self, user_input: str) -> str:
        logger.log_event("AGENT_START", {"input": user_input, "model": self.llm.model_name})
        prompt = f"User question: {user_input}"

        for step in range(1, self.max_steps + 1):
            result = self.llm.generate(prompt, system_prompt=self.get_system_prompt())
            content = result.get("content", "").strip()
            tracker.track_request(
                provider=result.get("provider", "unknown"),
                model=self.llm.model_name,
                usage=result.get("usage", {}),
                latency_ms=result.get("latency_ms", 0),
            )
            logger.log_event("AGENT_STEP", {"step": step, "content": content})

            final_answer = self.parse_final_answer(content)
            if final_answer:
                logger.log_event("AGENT_END", {"steps": step, "status": "success"})
                return final_answer

            action = self.parse_action(content)
            if not action:
                logger.log_event("AGENT_PARSE_ERROR", {"step": step, "content": content})
                prompt += (
                    f"\nAssistant:\n{content}\n"
                    "Observation: PARSE_ERROR. Use exactly Action: tool_name(arguments) or Final Answer: ..."
                )
                continue

            tool_name, args = action
            observation = self.execute_tool(tool_name, args)
            logger.log_event(
                "TOOL_CALL",
                {"step": step, "tool": tool_name, "args": args, "observation": observation},
            )
            prompt += f"\nAssistant:\n{content}\nObservation: {observation}"

        logger.log_event("AGENT_END", {"steps": self.max_steps, "status": "max_steps_exceeded"})
        return "I could not complete the task within the step limit."

    def execute_tool(self, tool_name: str, args: str) -> str:
        for tool in self.tools:
            if tool["name"] == tool_name:
                try:
                    return str(tool["function"](args))
                except Exception as exc:
                    return f"TOOL_ERROR: {exc}"
        return f"UNKNOWN_TOOL: {tool_name}"

    @staticmethod
    def parse_action(text: str) -> Optional[tuple[str, str]]:
        match = re.search(r"Action\s*:\s*([a-zA-Z_][\w]*)\((.*?)\)", text, re.DOTALL)
        if not match:
            return None
        return match.group(1).strip(), match.group(2).strip().strip("\"'")

    @staticmethod
    def parse_final_answer(text: str) -> Optional[str]:
        match = re.search(r"Final Answer\s*:\s*(.*)", text, re.DOTALL | re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()


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
    print(ReActAgent(llm=llm, tools=TOOLS).run(question))


if __name__ == "__main__":
    main()
