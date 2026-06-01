import inspect
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.core.llm_provider import LLMProvider
from src.telemetry.logger import logger
from src.telemetry.metrics import tracker


class ReActAgent:
    """
    ReAct-style agent implementing the Thought -> Action -> Observation loop.
    """

    def __init__(self, llm: LLMProvider, tools: List[Dict[str, Any]], max_steps: int = 5):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.history: List[Dict[str, str]] = []

    def get_system_prompt(self) -> str:
        tool_descriptions = "\n".join(
            [
                f"- {tool['name']}: {tool.get('description', 'No description provided.')}"
                for tool in self.tools
            ]
        )

        return f"""
You are a ReAct agent. Solve the user's task using a Thought, Action, Observation loop.

Available tools:
{tool_descriptions}

Rules:
- Use tools for calculations, lookup, file-like operations, or multi-step reasoning.
- Do not invent tool names.
- Do not write Observation yourself. Observation is provided by the runtime after a tool call.
- If no tool is needed, answer with Final Answer directly.
- Keep Thought short and useful.

Use exactly one of these formats:

Thought: your reasoning
Action: tool_name(arguments)

or:

Final Answer: your final answer
""".strip()

    def run(self, user_input: str) -> str:
        logger.log_event(
            "AGENT_START",
            {"input": user_input, "model": getattr(self.llm, "model_name", "unknown")},
        )

        current_prompt = f"User question: {user_input}"

        for step in range(1, self.max_steps + 1):
            result = self.llm.generate(
                current_prompt,
                system_prompt=self.get_system_prompt(),
            )

            content = result.get("content", "").strip()
            self.history.append({"role": "assistant", "content": content})

            tracker.track_request(
                provider=result.get("provider", "unknown"),
                model=getattr(self.llm, "model_name", "unknown"),
                usage=result.get("usage", {}),
                latency_ms=result.get("latency_ms", 0),
            )

            logger.log_event("AGENT_STEP", {"step": step, "content": content})

            final_answer = self._parse_final_answer(content)
            if final_answer:
                logger.log_event("AGENT_END", {"steps": step, "status": "success"})
                return final_answer

            action = self._parse_action(content)
            if action is None:
                logger.log_event(
                    "AGENT_PARSE_ERROR",
                    {"step": step, "content": content},
                )
                current_prompt += (
                    f"\n\nAssistant:\n{content}\n"
                    "Observation: PARSE_ERROR. Respond with exactly one valid "
                    "Action: tool_name(arguments) or Final Answer: ..."
                )
                continue

            tool_name, raw_args = action
            observation = self._execute_tool(tool_name, raw_args)

            logger.log_event(
                "TOOL_CALL",
                {
                    "step": step,
                    "tool": tool_name,
                    "args": raw_args,
                    "observation": observation,
                },
            )

            current_prompt += (
                f"\n\nAssistant:\n{content}\n"
                f"Observation: {observation}"
            )

        logger.log_event(
            "AGENT_END",
            {"steps": self.max_steps, "status": "max_steps_exceeded"},
        )
        return "I could not complete the task within the step limit."

    def _execute_tool(self, tool_name: str, raw_args: str) -> str:
        tool = self._find_tool(tool_name)
        if tool is None:
            return f"Tool '{tool_name}' not found. Available tools: {self._tool_names()}"

        func = tool.get("function")
        if not callable(func):
            return f"Tool '{tool_name}' has no callable function."

        try:
            parsed_args = self._parse_tool_args(raw_args)
            return str(self._call_tool(func, parsed_args))
        except Exception as exc:
            logger.log_event(
                "TOOL_ERROR",
                {"tool": tool_name, "args": raw_args, "error": str(exc)},
            )
            return f"Tool '{tool_name}' failed: {exc}"

    def _find_tool(self, tool_name: str) -> Optional[Dict[str, Any]]:
        for tool in self.tools:
            if tool.get("name") == tool_name:
                return tool
        return None

    def _tool_names(self) -> str:
        return ", ".join(tool.get("name", "<unnamed>") for tool in self.tools)

    def _parse_tool_args(self, raw_args: str) -> Any:
        args = raw_args.strip()

        if not args:
            return ""

        if (
            (args.startswith('"') and args.endswith('"'))
            or (args.startswith("'") and args.endswith("'"))
        ):
            return args[1:-1]

        if args.startswith("{") or args.startswith("["):
            try:
                return json.loads(args)
            except json.JSONDecodeError:
                return args

        return args

    def _call_tool(self, func: Any, parsed_args: Any) -> Any:
        signature = inspect.signature(func)
        parameters = signature.parameters

        if len(parameters) == 0:
            return func()

        if isinstance(parsed_args, dict):
            try:
                return func(**parsed_args)
            except TypeError:
                return func(parsed_args)

        return func(parsed_args)

    def _parse_action(self, text: str) -> Optional[Tuple[str, str]]:
        match = re.search(
            r"Action\s*:\s*([a-zA-Z_][\w]*)\s*\((.*?)\)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if match is None:
            return None

        tool_name = match.group(1).strip()
        args = match.group(2).strip()
        return tool_name, args

    def _parse_final_answer(self, text: str) -> Optional[str]:
        match = re.search(
            r"Final Answer\s*:\s*(.*)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if match is None:
            return None

        answer = match.group(1).strip()
        return answer or None