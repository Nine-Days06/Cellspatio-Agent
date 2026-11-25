"""Tool-calling runtime: LLM picks tool → WorkflowManager executes → HITL short-circuit.

Fallback strategy: no LLM or SDK exception → fall back to WorkflowManager.execute_workflow
legacy intent path (still served by IntentParser.parse / extract_parameters), ensuring
offline/tests work. Response without tool_calls returns general_response directly.
Optional router (ModalRouter) is used two ways when wired by CellSpatioAgent:
hint() injects modality/skill/best-practice hints into the system prompt every round;
read_analysis_plan() reads the static analysis plan once the LLM picks run_analysis.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

import openai

from src.config import env_int
from src.control.tools import (
    CONTINUE_TOOL_LOOP_FLAG,
    TERMINAL_STATUSES,
    build_system_prompt,
    build_tool_schemas,
    knowledge_tool_enabled,
)
from src.reasoning import split_delta

if TYPE_CHECKING:
    from src.control.router import ModalRouter

logger = logging.getLogger(__name__)

# 工具结果预算：单次结果先按上限截断，本轮累计超出后禁用工具、要求模型收口。
DEFAULT_TOOL_RESULT_MAX_CHARS = 20000
DEFAULT_TOOL_RESULT_BUDGET_CHARS = 60000
# 每次工具调用至少按该开销计入预算；即使结果很小，也能在有限轮内自然收口。
TOOL_CALL_OVERHEAD_CHARS = 1000
TOOL_RESULT_TRUNCATED_NOTE = "工具结果超出单次上限，已截断"
# 文献证据回灌模型时每条正文的上限；完整原文仅存在于持久化证据包与界面。
KNOWLEDGE_PAYLOAD_EVIDENCE_CHARS = 1200
KNOWLEDGE_PAYLOAD_TRUNCATED_MARK = "…（正文按条约 1,200 字压缩，完整片段见证据面板）"
KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
CITATION_INVALID = "citation_invalid"
UNAVAILABLE_MESSAGE = "知识检索服务暂时不可用，请稍后重试。"
CITATION_RETRY_NOTE = (
    "上一版回答未通过引用校验：至少引用一条本轮真实存在的 [E#] 编号，"
    "且不得出现不存在的编号；只使用对话中已返回的证据，不要再次调用工具。"
)
_CITATION_RE = re.compile(r"\[(E\d+)\]")
_CLAIMS_BLOCK_RE = re.compile(r"\n?```claims\s*(.*?)\s*```", re.DOTALL)
PUBLIC_EVIDENCE_FIELDS = (
    "evidence_id",
    "title",
    "year",
    "url",
    "kind",
    "source_id",
    "content",
)
PUBLIC_SOURCE_FIELDS = (
    "source_id",
    "title",
    "year",
    "url",
    "pmid",
    "doi",
    "species",
    "tissue",
    "technology",
    "evidence_ids",
)

REPEATED_TOOL_NOTE = (
    "（系统提示：检测到重复的工具调用，工具已停用。请直接基于已有工具结果回答，不要再次请求工具。）"
)
STRICT_TOOL_REJECT_NOTE = (
    "严格文献模式只允许 search_knowledge；请改用 search_knowledge 检索证据，其他工具不可用。"
)
BUDGET_EXHAUSTED_NOTE = (
    "（系统提示：本轮工具结果预算已用尽，工具已停用。请直接基于已有结果回答，不要再次请求工具。）"
)


# 协作式中止返回体。aborted 刻意不进 TERMINAL_STATUSES（src/control/tools.py:19），
# 由 chat_service 特判落库为「（已中断）」。
ABORTED_RESULT: dict[str, Any] = {
    "status": "aborted",
    "type": "general_response",
    "message": "已中断",
}

# 流式模式下的终态集合：needs_input/no_results 需让 LLM 再跑一轮生成面向用户的回复，
# 因此只是 TERMINAL_STATUSES 的子集；与枚举同步，避免两处手写清单漂移。
STREAM_TERMINAL_STATUSES: frozenset[str] = TERMINAL_STATUSES - {"needs_input", "no_results"}


@dataclass
class _KnowledgeTrace:
    """本轮检索事实：成功与否、查询、去重前的证据与来源。"""

    searched: bool = False
    unavailable: bool = False
    queries: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def absorb(self, result: dict[str, Any], query: str) -> None:
        """记录一次检索结果；失败时只标记不可用，不把错误当空证据。"""
        self.searched = True
        self.queries.append(str(result.get("query") or query))
        if result.get("status") != "success" or result.get("outcome") == KNOWLEDGE_UNAVAILABLE:
            self.unavailable = True
            return
        self.evidence.extend(result.get("evidence") or [])
        self.sources.extend(result.get("sources") or [])


class AgentRuntime:
    """LLM tool loop; workflow_manager provides *_for_agent execution face."""

    def __init__(self, llm_client, model: str, workflow_manager, router: ModalRouter | None = None):
        self.llm_client = llm_client
        self.model = model
        self.workflow_manager = workflow_manager
        self.router = router  # 分类/最佳实践提示注入

    def execute(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
        on_event: Callable[[dict], bool] | None = None,
        *,
        knowledge_mode: bool = False,
    ) -> dict[str, Any]:
        """执行一轮 tool-calling。

        on_event 为 None 时与改造前逐字节等价（create 不带 stream 键）。
        非 None 时走流式：逐 chunk 推 delta / tool_status；回调返回 False 视为
        客户端已断开，在下一个检查点（delta 之后、dispatch 之前）安全中止。

        循环不设固定轮数，以下任一条件收口：模型给出最终回答、人工终态、
        连续/重复的工具调用、工具结果累计预算用尽、客户端中止。
        工具结果可用 ``continue_tool_loop=True`` 声明本轮非终态，让模型带着结果
        继续推理；每次调用至少按 ``TOOL_CALL_OVERHEAD_CHARS`` 计入预算，
        即使结果很小也能在有限轮内收口。
        """
        context = dict(context) if context else {}
        context.setdefault("last_user_input", user_input)

        if self.llm_client is None:
            if knowledge_mode:
                return {
                    "status": "error",
                    "type": "knowledge_evidence",
                    "outcome": KNOWLEDGE_UNAVAILABLE,
                    "message": UNAVAILABLE_MESSAGE,
                }
            return self._legacy_fallback(user_input, context)

        hint = None if knowledge_mode else self._route_hint(user_input)
        messages = self._build_messages(
            user_input, context, hint=hint, knowledge_mode=knowledge_mode
        )
        schemas = build_tool_schemas(strict_literature=knowledge_mode)
        # 只有本轮已经检索过，最终回答才缓冲到校验后；未检索的普通对话保持逐字流式。
        buffering = knowledge_mode
        dispatched: dict[str, Any] | None = None
        reasoning_seen = False
        result_limit_chars = env_int("AGENT_TOOL_RESULT_MAX_CHARS", DEFAULT_TOOL_RESULT_MAX_CHARS)
        budget_chars = env_int("AGENT_TOOL_RESULT_BUDGET_CHARS", DEFAULT_TOOL_RESULT_BUDGET_CHARS)
        spent_chars = 0
        seen_signatures: set[str] = set()
        force_answer = False
        disconnected = False
        next_evidence_number = 1
        seen_chunks: set[str] = set()
        trace = _KnowledgeTrace()
        artifacts: dict[str, Any] = {}
        try:
            if knowledge_mode:
                forced_call = SimpleNamespace(
                    id="forced_search_knowledge",
                    type="function",
                    function=SimpleNamespace(
                        name="search_knowledge",
                        arguments=json.dumps({"query": user_input}, ensure_ascii=False),
                    ),
                )
                if on_event is not None and not on_event(
                    self._tool_status_event("search_knowledge", "start")
                ):
                    return dict(ABORTED_RESULT)
                result = self._dispatch(forced_call, user_input, context, knowledge_mode=True)
                seen_signatures.add(self._tool_signature(forced_call))
                result, added_evidence = self._merge_evidence(
                    result, next_evidence_number, seen_chunks
                )
                next_evidence_number += added_evidence
                trace.absorb(result, user_input)
                if on_event is not None and (
                    on_event(self._tool_status_event("search_knowledge", "end", result)) is False
                ):
                    disconnected = True
                if not trace.evidence:
                    return self._finalize_answer(
                        "",
                        messages,
                        schemas,
                        evidence=[],
                        sources=[],
                        queries=trace.queries,
                        searched=trace.searched,
                        unavailable=trace.unavailable,
                        on_event=on_event,
                        buffered=buffering,
                        artifacts=artifacts,
                    )
                dispatched = result
                self._collect_artifacts(artifacts, result)
                payload = self._tool_payload(
                    result, min(result_limit_chars, max(budget_chars - spent_chars, 0))
                )
                spent_chars += max(len(payload), TOOL_CALL_OVERHEAD_CHARS)
                if spent_chars >= budget_chars:
                    force_answer = True
                messages = messages + [
                    self._tool_message_dict(SimpleNamespace(content=""), forced_call),
                    {
                        "role": "tool",
                        "tool_call_id": forced_call.id,
                        "content": payload,
                    },
                ]
                if force_answer:
                    messages = messages + [{"role": "user", "content": BUDGET_EXHAUSTED_NOTE}]

            # 不设固定轮数：以最终回答、人工终态、重复调用、预算用尽或中止收口。
            while True:
                if disconnected:
                    return dict(ABORTED_RESULT)
                if on_event is None:
                    response = self.llm_client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=schemas,
                        temperature=0,
                        tool_choice="none" if force_answer else "auto",
                    )
                    message = response.choices[0].message
                    tool_calls = getattr(message, "tool_calls", None)
                else:
                    message, tool_calls, aborted, round_reasoning = self._stream_round(
                        messages,
                        on_event,
                        has_prior_reasoning=reasoning_seen,
                        tool_choice="none" if force_answer else "auto",
                        tools=schemas,
                        collect_only=buffering,
                    )
                    reasoning_seen = reasoning_seen or round_reasoning
                    if aborted:
                        return dict(ABORTED_RESULT)

                if not tool_calls:
                    content = message.content or ""
                    return self._finalize_answer(
                        content or "请尝试更具体的分析或知识问题。",
                        messages,
                        schemas,
                        evidence=trace.evidence,
                        sources=trace.sources,
                        queries=trace.queries,
                        searched=trace.searched,
                        unavailable=trace.unavailable,
                        on_event=on_event,
                        buffered=buffering,
                        artifacts=artifacts,
                    )

                if force_answer:
                    # 已禁用工具仍返回 tool_calls：视为异常协议，返回安全失败。
                    return {
                        "status": "error",
                        "type": "general_response",
                        "message": "模型在收口阶段仍请求工具，已停止；请缩小问题范围后重试。",
                    }

                # 一期契约：只执行第一个工具；是否立即返回见下方
                # 流式/非流式的终态集合判定（STREAM_TERMINAL_STATUSES）
                name = tool_calls[0].function.name
                signature = self._tool_signature(tool_calls[0])
                if signature in seen_signatures:
                    # 同一工具与参数已执行过：结果已在上下文中，不再执行副作用，强制收口。
                    logger.warning("repeated tool call detected: %s", name)
                    force_answer = True
                    messages = messages + [{"role": "user", "content": REPEATED_TOOL_NOTE}]
                    continue
                seen_signatures.add(signature)

                if on_event is not None and not on_event(self._tool_status_event(name, "start")):
                    return dict(ABORTED_RESULT)
                result = self._dispatch(
                    tool_calls[0], user_input, context, knowledge_mode=knowledge_mode
                )
                result, added_evidence = self._merge_evidence(
                    result, next_evidence_number, seen_chunks
                )
                next_evidence_number += added_evidence
                if name == "search_knowledge":
                    trace.absorb(result, user_input)
                    buffering = True
                dispatched = result
                self._collect_artifacts(artifacts, result)
                status = result.get("status")
                if result.get("analysis_type") == "single_cell":
                    # 固定单细胞链自带人工审批，不再回灌模型；但流式过程条必须收尾。
                    if on_event is not None:
                        on_event(self._tool_status_event(name, "end", result))
                    return result

                # 非流式：所有 TERMINAL_STATUSES 即刻返回（保持原有行为）
                # 流式：仅 STREAM_TERMINAL_STATUSES 为终态；
                # needs_input/no_results 需让 LLM 再跑一轮生成面向用户的回复。
                # continue_tool_loop=True 的工具自带“非终态”语义：检索失败也要回灌模型，
                # 让它如实说明不可用，而不是把 error 当最终回答直接返回。
                terminal_statuses = (
                    TERMINAL_STATUSES if on_event is None else STREAM_TERMINAL_STATUSES
                )
                if status in terminal_statuses and result.get(CONTINUE_TOOL_LOOP_FLAG) is not True:
                    if on_event is not None:
                        on_event(self._tool_status_event(name, "end", result))
                    return result
                # 非终态：发 end 事件并继续下一轮
                if on_event is not None:
                    # end 写失败说明客户端已断开：结果照常回灌，但下一轮模型调用前中止。
                    if on_event(self._tool_status_event(name, "end", result)) is False:
                        disconnected = True

                remaining_chars = budget_chars - spent_chars
                payload = self._tool_payload(
                    result, min(result_limit_chars, max(remaining_chars, 0))
                )
                spent_chars += max(len(payload), TOOL_CALL_OVERHEAD_CHARS)
                if spent_chars >= budget_chars:
                    # 预算用尽：保留已拿到的事实，下一轮禁用工具要求模型收口。
                    force_answer = True
                messages = messages + [
                    self._tool_message_dict(message, tool_calls[0]),
                    {
                        "role": "tool",
                        "tool_call_id": tool_calls[0].id,
                        "content": payload,
                    },
                ]
                if force_answer:
                    messages = messages + [{"role": "user", "content": BUDGET_EXHAUSTED_NOTE}]
        except Exception as e:  # noqa: BLE001 - SDK/网络异常
            if dispatched is not None:
                # 工具已执行（可能已落盘/入库），不能再跑旧工作流重复副作用。
                logger.warning("tool-calling failed after dispatch: %s", e)
                return {
                    "status": "error",
                    "type": dispatched.get("type", "general_response"),
                    "message": "工具已执行，但后续回复生成失败；请勿直接重复执行，先检查已有结果。",
                    "results": dispatched.get("results", {}),
                }
            if knowledge_mode:
                logger.warning("strict literature failed before dispatch: %s", e)
                return {
                    "status": "error",
                    "type": "knowledge_evidence",
                    "outcome": KNOWLEDGE_UNAVAILABLE,
                    "message": UNAVAILABLE_MESSAGE,
                }
            logger.warning("tool-calling failed, fallback to legacy workflow: %s", e)
            return self._legacy_fallback(user_input, context)

    def _legacy_fallback(self, user_input: str, context: dict[str, Any]) -> dict[str, Any]:
        """无 LLM/调用异常时的降级入口。

        默认链路已启用只读检索，旧工作流的成品回答协议不应再被触发；知识类问题
        直接报不可用，其他意图仍沿用旧的 intent 工作流（离线/测试依赖此路径）。
        """
        if knowledge_tool_enabled():
            from src.control.classifier import TaskClassifier

            if TaskClassifier().classify(user_input).get("modality") == "knowledge":
                return {
                    "status": "error",
                    "type": "general_response",
                    "message": "对话模型暂时不可用，本轮无法生成回答；请稍后重试。",
                }
        result = self.workflow_manager.execute_workflow(user_input, context)
        # 分类器可能漏判；旧工作流若仍返回成品回答，不能把它当作默认链路结果。
        if knowledge_tool_enabled() and result.get("type") == "knowledge_response":
            logger.warning("legacy knowledge workflow blocked after fallback")
            return {
                "status": "error",
                "type": "general_response",
                "message": "对话模型暂时不可用，本轮无法生成回答；请稍后重试。",
            }
        return result

    def _finalize_answer(
        self,
        content: str,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        *,
        evidence: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        queries: list[str],
        searched: bool,
        unavailable: bool,
        on_event: Callable[[dict], bool] | None,
        buffered: bool,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        """对用过检索的答案做三态收口，并在需要时缓冲后统一发射正文。

        ``artifacts`` 是本轮非检索工具累积的结果包；最终回答不能因为它们
        来自模型而被丢弃。
        """
        merged = dict(artifacts)
        if searched and not evidence:
            if unavailable:
                merged["knowledge_evidence"] = self._knowledge_package(
                    KNOWLEDGE_UNAVAILABLE, [], [], "", []
                )
                result = {
                    "status": "error",
                    "type": "general_response",
                    "outcome": KNOWLEDGE_UNAVAILABLE,
                    "message": UNAVAILABLE_MESSAGE,
                    "results": merged,
                }
            else:
                scope = "、".join(dict.fromkeys(q for q in queries if q))
                text = "当前知识库没有检索到相关证据。"
                if scope:
                    text += f"\n检索范围：{scope}。"
                merged["knowledge_evidence"] = self._knowledge_package(
                    INSUFFICIENT_EVIDENCE, [], [], "", []
                )
                result = {
                    "status": "success",
                    "type": "general_response",
                    "outcome": INSUFFICIENT_EVIDENCE,
                    "message": text,
                    "results": merged,
                }
            return self._emit_buffered(result, on_event, buffered)

        if searched and evidence:
            valid_ids = {str(item.get("evidence_id")) for item in evidence}
            content, claims = self._extract_claims(content, valid_ids)
            if not self._citations_valid(content, valid_ids):
                retried = self._retry_citation_answer(messages, content)
                retried_content, retried_claims = self._extract_claims(retried, valid_ids)
                if self._citations_valid(retried_content, valid_ids):
                    content, claims = retried_content, retried_claims
                else:
                    listing = self._evidence_listing(evidence)
                    merged["knowledge_evidence"] = self._knowledge_package(
                        CITATION_INVALID,
                        evidence,
                        sources,
                        "".join(f"[{item.get('evidence_id')}]" for item in evidence),
                        [],
                    )
                    result = {
                        "status": "error",
                        "type": "general_response",
                        "outcome": CITATION_INVALID,
                        "message": (
                            f"已检索到证据，但回答未通过引用校验。\n\n**已检索到的证据**\n{listing}"
                        ),
                        "results": merged,
                    }
                    return self._emit_buffered(result, on_event, buffered)

        result = {
            "status": "success",
            "type": "general_response",
            "message": content,
        }
        if searched:
            result["outcome"] = "supported_answer"
            merged["knowledge_evidence"] = self._knowledge_package(
                "supported_answer", evidence, sources, content, claims
            )
        if merged:
            result["results"] = merged
        return self._emit_buffered(result, on_event, buffered)

    @staticmethod
    def _knowledge_package(
        outcome: str,
        evidence: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        content: str,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """本轮证据包：证据/来源按白名单落库，claims 只取模型显式结构。"""
        return {
            "outcome": outcome,
            "evidence": [
                {key: item[key] for key in PUBLIC_EVIDENCE_FIELDS if key in item}
                for item in evidence
            ],
            "sources": [
                {key: source[key] for key in PUBLIC_SOURCE_FIELDS if key in source}
                for source in sources
            ],
            "citations": list(dict.fromkeys(_CITATION_RE.findall(content))),
            "claims": claims,
        }

    @staticmethod
    def _collect_artifacts(artifacts: dict[str, Any], result: dict[str, Any]) -> None:
        """累积非检索工具的结果包；证据包由收口阶段统一生成，不在此合并。"""
        payload = result.get("results")
        if isinstance(payload, dict):
            artifacts.update(
                {key: value for key, value in payload.items() if key != "knowledge_evidence"}
            )
        if result.get("run_id"):
            artifacts["run_id"] = result["run_id"]

    @staticmethod
    def _extract_claims(content: str, valid_ids: set[str]) -> tuple[str, list[dict[str, Any]]]:
        """剥离可选的 ```claims 块；只保留有效引用且显式给出的论断。"""
        match = _CLAIMS_BLOCK_RE.search(content)
        if not match:
            return content, []
        clean = (content[: match.start()] + content[match.end() :]).strip()
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("invalid claims block ignored: %s", exc)
            return clean, []
        if not isinstance(raw, list):
            return clean, []
        claims: list[dict[str, Any]] = []
        for item in raw[:20]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            ids = [
                str(evidence_id)
                for evidence_id in item.get("evidence_ids") or []
                if str(evidence_id) in valid_ids
            ]
            if text and ids:
                claims.append({"text": text, "evidence_ids": list(dict.fromkeys(ids))})
        return clean, claims

    @staticmethod
    def _emit_buffered(
        result: dict[str, Any],
        on_event: Callable[[dict], bool] | None,
        buffered: bool,
    ) -> dict[str, Any]:
        """缓冲模式下把最终正文一次性发给前端；断连按中止处理。"""
        if buffered and on_event is not None:
            if not on_event({"type": "delta", "text": result["message"]}):
                return dict(ABORTED_RESULT)
        return result

    @staticmethod
    def _citations_valid(content: str, valid_ids: set[str]) -> bool:
        """至少引用一条且所有引用都真实存在，才算通过引用校验。"""
        cited = set(_CITATION_RE.findall(content))
        return bool(cited) and cited <= valid_ids

    def _retry_citation_answer(self, messages: list[dict[str, Any]], invalid: str) -> str:
        """引用校验失败后，基于同一批证据要求模型重写一次（禁用工具）。"""
        try:
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=messages
                + [
                    {"role": "assistant", "content": invalid},
                    {"role": "user", "content": CITATION_RETRY_NOTE},
                ],
                temperature=0,
            )
            return response.choices[0].message.content or ""
        except (
            openai.APIError,
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning("citation retry failed: %s: %s", type(exc).__name__, exc)
            return ""

    @staticmethod
    def _evidence_listing(evidence: list[dict[str, Any]]) -> str:
        """把已检索证据渲染成可人工判断的简短清单，不含来源路径。"""
        lines = []
        for item in evidence:
            title = item.get("title") or item.get("url") or "证据"
            year = item.get("year") or ""
            snippet = str(item.get("content") or "")[:120]
            suffix = f"（{year}）" if year else ""
            lines.append(f"- [{item.get('evidence_id')}] {title}{suffix}：{snippet}")
        return "\n".join(lines)

    def _stream_round(
        self,
        messages: list[dict[str, Any]],
        on_event: Callable[[dict], bool],
        *,
        has_prior_reasoning: bool = False,
        tool_choice: str = "auto",
        tools: list[dict[str, Any]],
        collect_only: bool = False,
    ) -> tuple[Any, list | None, bool, bool]:
        """流式跑一轮 LLM 调用。

        返回 (message, tool_calls, aborted, reasoning_emitted)：message 为
        SimpleNamespace(content=..., tool_calls=[...])，形状与 SDK 非流式返回一致，
        可直接喂给既有 _dispatch / _tool_message_dict；aborted=True 时调用方须立即返回。

        第四项表示本轮是否产生过思考增量；``has_prior_reasoning`` 为真时，
        本轮首段思考前插入轻量分隔，让多轮工具调用之间的思考按顺序可辨。
        ``tool_choice="none"`` 用于预算/重复调用后的强制收口轮；``tools`` 由调用方
        按开关构建。``collect_only=True`` 时不发正文 delta（仅累积并返回），
        供引用校验前的最终回答缓冲；思考增量仍照常输出。
        """
        stream = self.llm_client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=0,
            tool_choice=tool_choice,
            stream=True,
        )
        content_parts: list[str] = []
        slots: dict[int, dict[str, Any]] = {}
        current_slot: int | None = None  # 追踪当前正在累积的槽位（用于无 index 的 chunk）
        reasoning_emitted = False
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue  # usage-only chunk
            delta = choices[0].delta
            reasoning_piece, piece = split_delta(delta)
            if reasoning_piece:
                text = reasoning_piece
                if has_prior_reasoning and not reasoning_emitted:
                    text = f"\n\n{text}"
                if not on_event({"type": "delta", "text": text, "source": "reasoning"}):
                    return None, None, True, reasoning_emitted
                reasoning_emitted = True
            if piece:
                content_parts.append(piece)
                if not collect_only and not on_event({"type": "delta", "text": piece}):
                    return None, None, True, reasoning_emitted
            for call in getattr(delta, "tool_calls", None) or []:
                # 防御性处理 index：OpenAI/Zhipu 兼容协议保证 tool_call chunk 恒带 index，
                # 但为兼容潜在异常 SDK，若缺失则沿用当前槽位，无当前槽则归入槽 0。
                raw_index = getattr(call, "index", None)
                if raw_index is not None:
                    slot_index = raw_index
                    current_slot = slot_index
                else:
                    slot_index = current_slot if current_slot is not None else 0
                slot = slots.setdefault(
                    slot_index,
                    {"id": None, "name": "", "arguments": ""},
                )
                if getattr(call, "id", None):
                    slot["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        slot["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments

        tool_calls = [
            SimpleNamespace(
                id=slot["id"],
                type="function",
                function=SimpleNamespace(name=slot["name"], arguments=slot["arguments"]),
            )
            for _index, slot in sorted(slots.items())
        ]
        message = SimpleNamespace(
            content="".join(content_parts),
            tool_calls=tool_calls or None,
        )
        return message, tool_calls or None, False, reasoning_emitted

    @staticmethod
    def _tool_signature(tool_call: Any) -> str:
        """工具名 + 规范化参数，用于检测重复调用。"""
        raw = getattr(tool_call.function, "arguments", None) or "{}"
        try:
            canonical = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, default=str)
        except (json.JSONDecodeError, TypeError):
            canonical = raw
        return f"{tool_call.function.name}\x00{canonical}"

    @staticmethod
    def _tool_status_event(
        name: str, phase: Literal["start", "end"], result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """过程条事件；检索工具结束时附带证据条数。label 由 SSE 层统一补。"""
        event: dict[str, Any] = {
            "type": "tool_status",
            "name": name,
            "phase": phase,
        }
        count = (result or {}).get("evidence_count")
        if phase == "end" and isinstance(count, int):
            event["count"] = count
        return event

    @staticmethod
    def _merge_evidence(
        result: dict[str, Any], start: int, seen_chunks: set[str]
    ) -> tuple[dict[str, Any], int]:
        """按 chunk 跨调用去重、重映射来源并接续 E 编号，返回结果与新增数。"""
        if result.get("type") != "knowledge_evidence" or result.get("status") != "success":
            return result, 0
        evidence = result.get("evidence") or []
        if not evidence:
            return result, 0
        mapping: dict[str, str] = {}
        renumbered: list[dict[str, Any]] = []
        for item in evidence:
            # chunk_id 缺失时退回内容哈希；两者都缺失则保留（测试桩/旧数据）。
            key = str(item.get("chunk_id") or item.get("content_sha256") or "")
            if key and key in seen_chunks:
                continue
            if key:
                seen_chunks.add(key)
            new_id = f"E{start + len(renumbered)}"
            mapping[item.get("evidence_id")] = new_id
            renumbered.append({**item, "evidence_id": new_id})
        if not renumbered:
            return (
                {
                    **result,
                    "evidence": [],
                    "sources": [],
                    "evidence_count": 0,
                    "message": "本次检索结果与已有证据重复，未新增证据；沿用原有 [E] 编号。",
                },
                0,
            )
        sources = [
            {
                **source,
                "evidence_ids": [
                    mapping[item] for item in source.get("evidence_ids") or [] if item in mapping
                ],
            }
            for source in result.get("sources") or []
        ]
        sources = [source for source in sources if source["evidence_ids"]]
        return (
            {
                **result,
                "evidence": renumbered,
                "sources": sources,
                "evidence_count": len(renumbered),
            },
            len(renumbered),
        )

    @classmethod
    def _tool_payload(cls, result: dict[str, Any], limit: int) -> str:
        """序列化工具结果：文献证据逐条压缩正文，其他结果超限时返回截断预览。"""
        payload = json.dumps(result, ensure_ascii=False, default=str)
        if len(payload) <= limit:
            return payload
        if result.get("type") == "knowledge_evidence":
            compacted = json.dumps(cls._compact_evidence(result), ensure_ascii=False, default=str)
            if len(compacted) <= limit:
                return compacted
        skeleton = {"truncated": True, "note": TOOL_RESULT_TRUNCATED_NOTE, "preview": ""}
        # 预留包装字段与转义余量，避免截断后反而明显超出上限。
        room = max(limit - len(json.dumps(skeleton, ensure_ascii=False)) - 64, 0)
        skeleton["preview"] = payload[:room]
        return json.dumps(skeleton, ensure_ascii=False)

    @staticmethod
    def _compact_evidence(result: dict[str, Any]) -> dict[str, Any]:
        """只压缩证据正文；编号、标题、年份、来源与来源关系原样保留。"""
        evidence = []
        for item in result.get("evidence") or []:
            if not isinstance(item, dict):
                evidence.append(item)
                continue
            content = item.get("content")
            if isinstance(content, str) and len(content) > KNOWLEDGE_PAYLOAD_EVIDENCE_CHARS:
                item = {
                    **item,
                    "content": content[:KNOWLEDGE_PAYLOAD_EVIDENCE_CHARS]
                    + KNOWLEDGE_PAYLOAD_TRUNCATED_MARK,
                    "content_truncated": True,
                }
            evidence.append(item)
        return {**result, "evidence": evidence}

    def _route_hint(self, user_input: str) -> dict[str, Any] | None:
        """调用 ModalRouter.hint；无 router 或失败时返回 None（不阻断主流程）。"""
        from src.control.classifier import TaskClassifier

        classification = TaskClassifier().classify(user_input)
        if classification.get("skill") == "single_cell":
            return self._with_exposed_skill({**classification, "best_practices": None})
        if self.router is None:
            return None
        try:
            return self._with_exposed_skill(self.router.hint(user_input))
        except Exception as e:  # noqa: BLE001 - 提示注入失败必须可降级
            logger.warning("route hint failed, continue without: %s", e)
            return None

    @staticmethod
    def _with_exposed_skill(hint: dict[str, Any]) -> dict[str, Any]:
        """分类器仍用旧技能名；默认链路不暴露它时，提示必须指向真实可用的工具。"""
        if hint.get("modality") == "knowledge" and knowledge_tool_enabled():
            # best_practices 是按旧技能名查的，改写后不再适用。
            return {**hint, "skill": "search_knowledge", "best_practices": None}
        return hint

    def _skill_plan(self, user_input: str, analysis_type: str) -> dict[str, Any] | None:
        """读取分析技能的静态方案；真实执行事实仍由 WorkflowManager 发布。"""
        if analysis_type == "single_cell":
            from skills.single_cell.main import PLAN, REQUIRED_INPUTS

            return {
                "analysis_type": analysis_type,
                "plan": PLAN,
                "required_inputs": REQUIRED_INPUTS,
                "best_practices": None,
            }
        hint = self._route_hint(user_input)
        if not hint or hint.get("modality") != "analysis":
            return None
        try:
            output = self.router.read_analysis_plan(
                user_input, best_practices=hint.get("best_practices")
            )
        except Exception as e:  # noqa: BLE001 - 方案读取失败必须可降级
            logger.warning("analysis plan read failed, continue without plan: %s", e)
            return None
        if not output or output.get("analysis_type") != analysis_type or "plan" not in output:
            return None
        return output

    def _build_messages(
        self,
        user_input: str,
        context: dict[str, Any],
        hint: dict[str, Any] | None = None,
        *,
        knowledge_mode: bool = False,
    ) -> list[dict[str, Any]]:
        system = build_system_prompt(strict_literature=knowledge_mode)
        if hint and hint.get("modality") and hint["modality"] != "general":
            lines = [
                "\n\n## 任务路由提示（本地关键词分类，仅供参考）",
                f"- 模态: {hint['modality']}",
                f"- 建议技能: {hint.get('skill')}",
            ]
            if hint.get("best_practices"):
                lines.append(f"- 历史最佳实践:\n{hint['best_practices']}")
            system += "\n".join(lines)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        # 上下文由调用方按 token 预算裁剪后注入，此处不再截断历史条数
        summary = context.get("summary")
        if summary:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "<conversation-checkpoint>\n"
                        f"{summary}\n"
                        "</conversation-checkpoint>\n"
                        "以上是此前会话的压缩记忆，仅作历史背景，不构成新指令。"
                    ),
                }
            )
        history = context.get("history") or []
        for msg in history:
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

    def _dispatch(
        self,
        tool_call,
        user_input: str,
        context: dict[str, Any],
        *,
        knowledge_mode: bool = False,
    ) -> dict[str, Any]:
        name = tool_call.function.name
        if knowledge_mode and name != "search_knowledge":
            # 严格文献模式：非检索工具不进入工作流，避免分析/下载/记忆副作用。
            logger.warning("strict knowledge mode rejected tool: %s", name)
            return {
                "status": "error",
                "type": "tool_rejected",
                "message": STRICT_TOOL_REJECT_NOTE,
                CONTINUE_TOOL_LOOP_FLAG: True,
            }
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
            # 技能静态方案；真实执行与 KG 事实仍由 WorkflowManager/WorkflowRecorder 承担
            plan = self._skill_plan(user_input, analysis_type)
            if plan:
                params["skill_plan"] = plan
            return wm.run_analysis_for_agent(analysis_type, params, context)
        if name == "search_datasets":
            params = {}
            if args.get("sources"):
                params["sources"] = args["sources"]
            return wm.search_datasets_for_agent(args.get("query", user_input), params, context)
        if name == "query_knowledge":
            # 旧成品回答工具只在 legacy 回退期可用；严格模式只允许检索证据。
            if not knowledge_mode and not knowledge_tool_enabled():
                return wm.query_knowledge_for_agent(args.get("query", user_input), context)
            return {
                "status": "error",
                "type": "general_response",
                "message": "query_knowledge 已弃用，请改用 search_knowledge 检索证据。",
                # 回灌给模型，让它改调正确工具，而不是让整轮直接失败。
                "continue_tool_loop": True,
            }
        if name == "search_knowledge" and knowledge_tool_enabled(force=knowledge_mode):
            return wm.search_knowledge_for_agent(args.get("query", user_input), context)
        if name == "query_memory":
            return wm.query_memory_for_agent(args.get("query", user_input), context)
        return {
            "status": "error",
            "type": "general_response",
            "message": f"未知工具: {name}",
        }
