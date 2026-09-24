"""Tool-calling runtime: LLM picks tool → WorkflowManager executes → HITL short-circuit.

Fallback strategy (Phase 1): no LLM, SDK exception, or response without tool_calls
and unparsable → fall back to WorkflowManager.execute_workflow legacy intent path,
ensuring offline/tests work. Phase 2 tightens after IntentParser removal.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from src.control.tools import SYSTEM_PROMPT, TERMINAL_STATUSES, TOOL_SCHEMAS

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3
MAX_HISTORY = 10


class AgentRuntime:
    """LLM tool loop; workflow_manager provides *_for_agent execution face."""

    def __init__(self, llm_client, model: str, workflow_manager):
        self.llm_client = llm_client
        self.model = model
        self.workflow_manager = workflow_manager

    def execute(self, user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context) if context else {}
        context.setdefault("last_user_input", user_input)

        if self.llm_client is None:
            return self.workflow_manager.execute_workflow(user_input, context)

        messages = self._build_messages(user_input, context)
        try:
            for _ in range(MAX_ROUNDS):
                response = self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    temperature=0,
                    tool_choice="auto",
                )
                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", None)

                if not tool_calls:
                    content = message.content or ""
                    return {
                        "status": "success",
                        "type": "general_response",
                        "message": content or "请尝试更具体的分析或知识问题。",
                    }

                # Phase 1 contract: execute ONLY first tool, HITL/terminal returns immediately
                result = self._dispatch(tool_calls[0], user_input, context)
                status = result.get("status")
                if status in TERMINAL_STATUSES:
                    return result

                messages = messages + [
                    self._tool_message_dict(message, tool_calls[0]),
                    {
                        "role": "tool",
                        "tool_call_id": tool_calls[0].id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    },
                ]
            return {
                "status": "error",
                "type": "general_response",
                "message": f"已达最大工具调用轮数（{MAX_ROUNDS}），请简化请求后重试。",
            }
        except Exception as e:  # noqa: BLE001 - SDK/network failure falls back to legacy
            logger.warning("tool-calling failed, fallback to legacy workflow: %s", e)
            return self.workflow_manager.execute_workflow(user_input, context)

    def _build_messages(self, user_input: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        history = context.get("history") or []
        for msg in history[-MAX_HISTORY:]:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_input})
        return messages

    @staticmethod
    def _tool_message_dict(assistant_message, tool_call) -> dict[str, Any]:
        """Convert SDK assistant+tool_calls back to dict for message history."""
        return {
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
            ],
        }

    def _dispatch(self, tool_call, user_input: str, context: dict[str, Any]) -> dict[str, Any]:
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            return {
                "status": "error",
                "type": "general_response",
                "message": "工具参数不是合法 JSON，请换个说法重试。",
            }

        wm = self.workflow_manager
        if name == "run_analysis":
            from src.control.intent_parser import IntentParser

            analysis_type = args.get("analysis_type") or ""
            params = IntentParser().extract_parameters(user_input)
            if args.get("question"):
                params["question"] = args["question"]
            return wm.run_analysis_for_agent(analysis_type, params, context)
        if name == "search_datasets":
            params = {}
            if args.get("sources"):
                params["sources"] = args["sources"]
            return wm.search_datasets_for_agent(args.get("query", user_input), params, context)
        if name == "query_knowledge":
            return wm.query_knowledge_for_agent(args.get("query", user_input), context)
        return {
            "status": "error",
            "type": "general_response",
            "message": f"未知工具: {name}",
        }