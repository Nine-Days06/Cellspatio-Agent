"""对话服务：把 Streamlit run_prompt 的编排平移到 API 层，负责落库时机与事件发射。"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from typing import Any, Literal

from src.api.events import chart_events, make_event, wire_message
from src.api.session_locks import lock_for
from src.chat_messages import (
    build_assistant_message,
    format_script_confirmation,
    make_llm_summarize,
)
from src.control import compact
from src.ui.confirm_protocol import STATUS_PENDING
from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)

# 兼容既有 `from src.api.chat_service import session_lock` 导入；锁实现统一在 session_locks。
session_lock = lock_for

Emit = Callable[[dict], bool]
BeforeDone = Callable[[SessionStore, str], None]
PreDone = Callable[[dict[str, Any]], list[dict[str, Any]]]

# 只有这些状态才算“这一轮正常完成”；中断与报错的首轮不触发自动命名。
_INCOMPLETE_STATUSES = frozenset({"aborted", "error", "interrupted"})


def turn_completed(result: dict[str, Any]) -> bool:
    """本轮是否正常完成（用于只在成功首轮接线的扩展点）。"""
    return result.get("status") not in _INCOMPLETE_STATUSES


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


def persist_message(
    store: SessionStore,
    session_id: str,
    content: str,
    results: dict[str, Any] | None = None,
    pending_script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加助手消息并读回末条（拿 id 与解码后的 results）。"""
    return store.append_message(
        session_id, "assistant", content, results=results, pending_script=pending_script
    )


# 兼容既有 `_persist` 导入；新代码用 persist_message。
_persist = persist_message


def finalize_turn(
    store: SessionStore,
    session_id: str,
    *,
    content: str,
    results: dict[str, Any] | None = None,
    pending_script: dict[str, Any] | None = None,
    emit: Emit | None = None,
    events_before_done: PreDone | None = None,
    before_done: BeforeDone | None = None,
) -> dict[str, Any]:
    """所有分支共用的轮次收尾：落库助手消息 → 附加事件 → before_done → done。

    ``events_before_done`` 用落库消息构造需带 message_id 的事件（确认卡）；
    ``before_done`` 是“助手消息已落库、done 未发射”的唯一扩展点（会话自动命名在此接线）。
    """
    last = persist_message(
        store, session_id, content, results=results, pending_script=pending_script
    )
    if emit is None:
        return last
    for chart in chart_events(results):
        emit(chart)
    if events_before_done is not None:
        for event in events_before_done(last):
            emit(event)
    if before_done is not None:
        before_done(store, session_id)
    emit(make_event("done", message_id=last["id"], message=wire_message(last)))
    return last


def persist_outcome(
    store: SessionStore,
    session_id: str,
    result: dict[str, Any],
    *,
    emit: Emit | None = None,
    before_done: BeforeDone | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """落库助手终态并按需发射 chart 与 done；返回 (result, last)。"""
    message = build_assistant_message(result)
    last = finalize_turn(
        store,
        session_id,
        content=message["content"],
        results=result.get("results"),
        emit=emit,
        before_done=before_done,
    )
    return result, last


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
    finalize_turn(store, session_id, content=answer, results={"knowledge_qa": pkg})
    return {
        "status": "interrupted",
        "type": "knowledge_qa",
        "message": answer,
        "results": {"knowledge_qa": pkg},
    }


def is_qc_stage(result: dict[str, Any]) -> bool:
    """固定 QC 终态判定：对话与本地入口共用，避免两处条件漂移。"""
    return result.get("stage") == "qc_review"


def register_qc_result(
    agent: Any, store: SessionStore, session_id: str, result: dict[str, Any]
) -> dict[str, Any]:
    """把成功的固定 QC 结果登记为可交互 run；失败转结构化 error。

    对话入口与本地输入入口共用，确保两条路径的证据校验完全一致。
    """
    if not is_qc_stage(result) or result.get("status") != "needs_confirmation":
        return result

    from pathlib import Path

    from src.analysis.qc_preview import register_evidence
    from src.ui.analysis_roots import AnalysisRoots

    try:
        directory = Path(result["results"]["qc_artifacts"]["metadata.json"]).resolve().parent
        roots = getattr(agent, "analysis_roots", None)
        if roots is None:
            recorder = getattr(getattr(agent, "workflow_manager", None), "workflow_recorder", None)
            repo_root = Path(recorder.repo_root) if recorder else Path.cwd()
            roots = AnalysisRoots(store, repo_root)
        root = roots.for_session(session_id) / "sc_qc"
        evidence = register_evidence(directory, root)
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
    return result


def run_turn(
    agent: Any,
    store: SessionStore,
    session_id: str,
    prompt: str,
    emit: Emit,
    mode: Literal["chat", "knowledge_qa"] = "chat",
    before_done: BeforeDone | None = None,
) -> dict[str, Any]:
    """执行一轮对话并返回运行时结果。

    步骤（平移 src/ui/app.py:36 run_prompt，用 SessionStore 替代 st.session_state）：
    落用户消息 → 压缩上下文 → 跑 AgentRuntime（on_event 直连 emit）→ 按分支落库
    并发射 confirm_card / chart / done。

    emit(event) -> bool：返回 False 表示客户端已断开，后续发射应被忽略。
    before_done：助手消息落库后、``done`` 发射前的唯一扩展点；中断与报错分支不触发。
    """
    question = store.append_message(session_id, "user", prompt)

    # 知识问答必须在历史压缩与上下文构造之前分叉：只允许本轮问题进入模型。
    if mode == "knowledge_qa":
        from src.knowledge.qa import STAGE_RETRIEVAL, run_knowledge_qa

        question_message_id = question["id"]
        connected = True

        def on_stage(stage: str, phase: str) -> bool:
            """把问答阶段翻译成现有工具状态事件；返回连接是否仍可写。"""
            nonlocal connected
            label = "检索知识库" if stage == STAGE_RETRIEVAL else "生成回答"
            connected = emit(
                make_event(
                    "tool_status",
                    name=f"knowledge_qa_{stage}",
                    phase=phase,
                    label=label,
                )
            )
            return connected

        def on_reasoning(text: str) -> bool:
            """思考增量走同一 delta 事件，只加 source 标记，不写正文草稿。"""
            nonlocal connected
            if not connected:
                return False
            connected = emit(make_event("delta", text=text, source="reasoning"))
            return connected

        # shortcut: 检索开始前断连直接放弃；非流式生成不可取消，流式思考
        # 回写失败时由 on_reasoning 返回 False 提前收口。
        qa = run_knowledge_qa(
            getattr(agent, "knowledge_client", None),
            prompt,
            session_id=session_id,
            question_message_id=question_message_id,
            on_stage=on_stage,
            on_reasoning=on_reasoning,
            streaming=True,
        )
        if not connected or qa.get("status") == "interrupted":
            return _persist_interrupted_qa(store, session_id, prompt, question_message_id, qa)
        finalize_turn(
            store,
            session_id,
            content=qa["answer"],
            results={"knowledge_qa": qa},
            emit=emit,
            before_done=before_done if turn_completed({"status": qa["status"]}) else None,
        )
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
        "session_id": session_id,
    }
    workspace = store.get_workspace(store.workspace_id) if store.workspace_id else None
    if workspace and workspace.get("kind") == "ephemeral":
        # 临时访客只能分析自己上传进工作区的数据，聊天里提到的服务器路径同样拒绝。
        context["allowed_input_root"] = workspace.get("root_path")
    result = agent.agent_runtime.execute(prompt, context, on_event=emit)

    if result.get("status") == "aborted":
        finalize_turn(store, session_id, content="（已中断）", emit=emit)
        return result

    if result.get("status") == "needs_script_confirmation":
        pending = {
            "script": result.get("script", ""),
            "analysis_type": result.get("analysis_type"),
            "params": result.get("params") or {},
            "method_context": result.get("method_context"),
            "user_request": prompt,
            "status": STATUS_PENDING,
        }
        if result.get("run_id"):
            pending["run_id"] = result["run_id"]
        finalize_turn(
            store,
            session_id,
            content=format_script_confirmation(result),
            pending_script=pending,
            emit=emit,
            events_before_done=lambda last: [
                make_event(
                    "confirm_card", kind="script", payload={"message_id": last["id"], **pending}
                )
            ],
        )
        return result

    if result.get("type") == "fetch_data" and result.get("status") == "needs_confirmation":
        text = result.get("message", "找到候选数据集，请选择要下载的项：")
        # 候选只放事件 payload 不落库（与 Streamlit session_state 语义一致）
        finalize_turn(
            store,
            session_id,
            content=text,
            emit=emit,
            events_before_done=lambda last: [
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
            ],
        )
        return result

    result = register_qc_result(agent, store, session_id, result)

    result, _ = persist_outcome(
        store,
        session_id,
        result,
        emit=emit,
        before_done=before_done if turn_completed(result) else None,
    )
    return result
