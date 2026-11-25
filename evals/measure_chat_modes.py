"""Phase 0 基线：会话模式切换/切回，以及文献回答后继续分析的比例。

历史消息没有 ``results.chat_mode`` 时按旧协议推断：带 ``knowledge_qa`` 视为问答模式，
其余视为智能对话。新消息由 chat_service 显式写入模式，后续统计不再推断。

用法：
  python -m evals.measure_chat_modes --out .scratch/eval/mode_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui.session_store import SessionStore  # noqa: E402

ANALYSIS_RESULT_KEYS = ("charts", "qc_artifacts", "analysis_type", "qc_run_id")


def _mode_of(message: dict[str, Any]) -> str | None:
    """读取显式模式；旧 ``knowledge_qa`` 包推断为问答，有结果无包推断为对话。"""
    results = message.get("results") or {}
    if results.get("chat_mode"):
        return str(results["chat_mode"])
    if results.get("knowledge_qa"):
        return "knowledge_qa"
    return "chat" if results else None


def _has_analysis(message: dict[str, Any]) -> bool:
    results = message.get("results") or {}
    return any(key in results for key in ANALYSIS_RESULT_KEYS)


def measure(store: SessionStore) -> dict[str, Any]:
    """统计已知模式轮次、会话内切换与文献回答后的分析比例。"""
    sessions = store.list_sessions(include_archived=True)
    turns = {"chat": 0, "knowledge_qa": 0}
    switch_sessions = 0
    switch_back = 0
    knowledge_turns = 0
    knowledge_then_analysis = 0
    for session in sessions:
        messages = store.get_messages(session["id"])
        assistant = [message for message in messages if message.get("role") == "assistant"]
        modes = [mode for mode in (_mode_of(message) for message in assistant) if mode]
        for mode in modes:
            turns[mode] = turns.get(mode, 0) + 1
        if len(set(modes)) > 1:
            switch_sessions += 1
        for previous, current in zip(modes, modes[1:], strict=False):
            if previous == "knowledge_qa" and current == "chat":
                switch_back += 1
        for index, message in enumerate(assistant):
            if _mode_of(message) != "knowledge_qa":
                continue
            knowledge_turns += 1
            if any(_has_analysis(later) for later in assistant[index + 1 :]):
                knowledge_then_analysis += 1
    return {
        "sessions": len(sessions),
        "assistant_turns": turns,
        "sessions_with_mode_switch": switch_sessions,
        "knowledge_to_chat_switches": switch_back,
        "knowledge_turns": knowledge_turns,
        "knowledge_then_analysis": knowledge_then_analysis,
        "knowledge_then_analysis_ratio": round(knowledge_then_analysis / knowledge_turns, 4)
        if knowledge_turns
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    """读取会话库并输出基线 JSON。"""
    parser = argparse.ArgumentParser(description="会话模式使用基线")
    parser.add_argument("--db", default=None, help="会话数据库路径；缺省用项目默认库")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    report = measure(SessionStore(args.db))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
