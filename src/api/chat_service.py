"""对话服务：把 Streamlit run_prompt 的编排平移到 API 层，负责落库时机与事件发射。"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Any, Literal

from src.api.events import chart_events, make_event, wire_message
from src.control import compact
from src.control.chat_messages import (
    build_assistant_message,
    format_script_confirmation,
    make_llm_summarize,
)
from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

Emit = Callable[[dict], bool]


def session_lock(session_id: str) -> threading.Lock:
    """取该会话的生成锁；单用户单进程内存锁，每会话同时只允许一个生成任务。"""
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _locks[session_id] = lock
        return lock


def soft_warn_for(store: SessionStore, session_id: str) -> bool:
    """会话上下文软阈值判定——REST 横幅与 SSE compressed 的唯一口径。

    统一 offset 口径：summary + prepare_history(全量消息)[summary_upto:]，
    与 run_turn 压缩后的实际窗口一致；会话不存在返回 False（404 由调用方处理）。
    """
    session = store.get_session(session_id)
    if session is None:
        return False
    prepared = compact.prepare_history(store.get_messages(session_id))
    offset = session.get("summary_upto") or 0
    return bool(compact.should_soft_warn(session.get("summary"), prepared[offset:]))


def _persist(
    store: SessionStore,
    session_id: str,
    content: str,
    results: dict[str, Any] | None = None,
    pending_script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加助手消息并读回末条（拿 id 与解码后的 results）。"""
    store.append_message(
        session_id, "assistant", content, results=results, pending_script=pending_script
    )
    return store.get_messages(session_id)[-1]


def _persist_interrupted_qa(
    store: SessionStore,
    session_id: str,
    question: str,
    question_message_id: int,
    qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """断连时也落终态：刷新后能看出这一轮被中断，而不是只有用户提问没有回应。"""
    from src.knowledge.qa import interrupted_package

    # 已有检索/生成结果时保留内容，只把状态改为 interrupted，避免白跑一轮
    pkg = (
        {**qa, "status": "interrupted"}
        if qa
        else interrupted_package(
            question, session_id=session_id, question_message_id=question_message_id
        )
    )
    answer = str(pkg.get("answer") or "本轮已中断，未生成回答。")
    _persist(store, session_id, answer, results={"knowledge_qa": pkg})
    return {
        "status": "interrupted",
        "type": "knowledge_qa",
        "message": answer,
        "results": {"knowledge_qa": pkg},
    }


def run_turn(
    agent: Any,
    store: SessionStore,
    session_id: str,
    prompt: str,
    emit: Emit,
    mode: Literal["chat", "knowledge_qa"] = "chat",
) -> dict[str, Any]:
    """执行一轮对话并返回运行时结果。

    步骤（平移 src/ui/app.py:36 run_prompt，用 SessionStore 替代 st.session_state）：
    落用户消息 → 压缩上下文 → 跑 AgentRuntime（on_event 直连 emit）→ 按分支落库
    并发射 confirm_card / chart / done。

    emit(event) -> bool：返回 False 表示客户端已断开，后续发射应被忽略。
    """
    store.append_message(session_id, "user", prompt)

    # 知识问答必须在历史压缩与上下文构造之前分叉：只允许本轮问题进入模型。
    if mode == "knowledge_qa":
        from src.knowledge.qa import run_knowledge_qa

        question_message_id = store.get_messages(session_id)[-1]["id"]
        # shortcut: 只在开始前和事件发射处检查断连；进行中的检索/生成不会取消
        if not emit(
            make_event("tool_status", name="knowledge_qa", phase="start", label="检索知识库")
        ):
            return _persist_interrupted_qa(store, session_id, prompt, question_message_id)
        qa = run_knowledge_qa(
            getattr(agent, "knowledge_client", None),
            prompt,
            session_id=session_id,
            question_message_id=question_message_id,
        )
        if not emit(make_event("tool_status", name="knowledge_qa", phase="end")):
            return _persist_interrupted_qa(store, session_id, prompt, question_message_id, qa)
        last = _persist(store, session_id, qa["answer"], results={"knowledge_qa": qa})
        emit(make_event("done", message_id=last["id"], message=wire_message(last)))
        return {
            "status": qa["status"],
            "type": "knowledge_qa",
            "message": qa["answer"],
            "results": {"knowledge_qa": qa},
        }

    session = store.get_session(session_id) or {}
    messages = store.get_messages(session_id)

    prepared = compact.prepare_history(messages[:-1])
    summary, offset = compact.maybe_compact(
        session.get("summary"),
        session.get("summary_upto") or 0,
        prepared,
        make_llm_summarize(agent),
    )
    store.update_session_meta(session_id, summary=summary, summary_upto=offset)
    _older, recent = compact.select_recent(prepared[offset:])
    if soft_warn_for(store, session_id):
        emit(make_event("compressed", soft_warn=True))

    context = {
        "history": recent,
        "summary": summary,
        "downloaded_assets": session.get("downloaded_assets") or [],
        "last_user_input": prompt,
    }
    result = agent.agent_runtime.execute(prompt, context, on_event=emit)

    if result.get("status") == "aborted":
        last = _persist(store, session_id, "（已中断）")
        emit(make_event("done", message_id=last["id"], message=wire_message(last)))
        return result

    if result.get("status") == "needs_script_confirmation":
        pending = {
            "script": result.get("script", ""),
            "analysis_type": result.get("analysis_type"),
            "params": result.get("params") or {},
            "method_context": result.get("method_context"),
            "user_request": prompt,
            "status": "pending",
        }
        last = _persist(
            store, session_id, format_script_confirmation(result), pending_script=pending
        )
        emit(
            make_event("confirm_card", kind="script", payload={"message_id": last["id"], **pending})
        )
        emit(make_event("done", message_id=last["id"], message=wire_message(last)))
        return result

    if result.get("type") == "fetch_data" and result.get("status") == "needs_confirmation":
        text = result.get("message", "找到候选数据集，请选择要下载的项：")
        # 候选只放事件 payload 不落库（与 Streamlit session_state 语义一致）
        last = _persist(store, session_id, text)
        emit(
            make_event(
                "confirm_card",
                kind="data",
                payload={
                    "message_id": last["id"],
                    "candidates": result.get("candidates") or [],
                    "query": result.get("query", ""),
                    "message": text,
                },
            )
        )
        emit(make_event("done", message_id=last["id"], message=wire_message(last)))
        return result

    if result.get("stage") == "qc_review" and result.get("status") == "needs_confirmation":
        from pathlib import Path

        from src.analysis.qc_preview import register_evidence
        from src.api.routes_qc import qc_root

        try:
            directory = Path(result["results"]["qc_artifacts"]["metadata.json"]).resolve().parent
            evidence = register_evidence(directory, qc_root(agent))
            store.register_qc_run(session_id, directory.name, str(directory), evidence)
            result["results"]["qc_run_id"] = directory.name
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            result = {
                "status": "error",
                "analysis_type": "single_cell",
                "stage": "qc_review",
                "message": f"QC 会话关联失败，不能预览或批准：{exc}",
                "results": {"qc_artifacts": result.get("results", {}).get("qc_artifacts", {})},
            }

    message = build_assistant_message(result)
    last = _persist(store, session_id, message["content"], results=result.get("results"))
    for chart in chart_events(result.get("results")):
        emit(chart)
    emit(make_event("done", message_id=last["id"], message=wire_message(last)))
    return result
