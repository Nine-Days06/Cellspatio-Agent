"""聊天消息构造纯函数：从 app.py 搬移，供 Streamlit 与 API 层共用。

无 Streamlit 依赖、无状态，便于单测与跨前端复用。
"""
from __future__ import annotations

from typing import Any

from src.control import compact


def make_llm_summarize(agent: Any):
    """按 agent 的 LLM 客户端构造摘要调用器；无客户端时返回 None（跳过压缩）。"""
    client = getattr(agent, "llm_client", None)
    if client is None:
        return None
    model = getattr(agent, "llm_model", "") or ""

    def _summarize(prior, older):
        return compact.summarize(client, model, prior, older)

    return _summarize


def summarize_result(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """结果 → (聊天文本, 引用列表)。当次渲染与历史落盘共用，保证文本一致。"""
    references: list[dict[str, Any]] = []
    if result.get("type") == "knowledge_response":
        response = result.get("response", "无响应")
        references = list(result.get("references") or [])
    elif result.get("type") == "fetch_result":
        asset = result.get("asset", {})
        response = f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get("results"):
        response = f"分析完成: {result.get('message', '')}"
        if result.get("capsule_dir"):
            response += f"\n\n复现胶囊：`{result['capsule_dir']}`"
    else:
        response = result.get("message", "处理完成")
    explanation = result.get("explanation")
    if explanation:
        response = f"{response}\n\n---\n**结果解读**\n\n{explanation}"
    if references:
        from src.knowledge.lightrag_client import format_reference

        lines = "\n".join(f"- {format_reference(r)}" for r in references)
        response = f"{response}\n\n**来源**\n{lines}"
    return response, references


def build_assistant_message(result: dict[str, Any]) -> dict[str, Any]:
    """构造 assistant 历史消息；含分析结果时附加 results 供重放渲染"""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": summarize_result(result)[0],
    }
    if result.get("results"):
        msg["results"] = result["results"]
    return msg


def format_script_confirmation(result: dict[str, Any]) -> str:
    """将脚本确认结果格式化为聊天消息文本"""
    return (
        f"{result.get('message', '已生成 R 脚本，请审阅并确认执行')}\n\n"
        f"```r\n{result.get('script', '')}\n```"
    )
