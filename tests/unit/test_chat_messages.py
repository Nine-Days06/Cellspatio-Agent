"""chat_messages 纯函数：与搬迁前 app.py 私有函数行为逐项等价。"""
from __future__ import annotations

from src.control import chat_messages


def test_summarize_result_knowledge_response_with_references():
    result = {
        "type": "knowledge_response",
        "response": "TP53 是抑癌基因",
        "references": [{"title": "T1", "authors": ["A"], "year": 2020}],
    }

    content, refs = chat_messages.summarize_result(result)

    assert "TP53 是抑癌基因" in content
    assert "**来源**" in content
    assert refs == result["references"]


def test_summarize_result_analysis_with_capsule_and_explanation():
    result = {
        "results": {"data": []},
        "message": "分析完成",
        "capsule_dir": "snapshots/run-1",
        "explanation": "差异基因 42 个",
    }

    content, refs = chat_messages.summarize_result(result)

    assert content.startswith("分析完成: 分析完成")
    assert "复现胶囊：`snapshots/run-1`" in content
    assert "**结果解读**" in content
    assert refs == []


def test_summarize_result_fetch_result_lists_access_path():
    result = {"type": "fetch_result",
              "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/x.gz"}}

    content, _refs = chat_messages.summarize_result(result)

    assert "已下载 GSE1 → `data/raw/geo/GSE1/x.gz`" in content


def test_summarize_result_plain_message_default():
    assert chat_messages.summarize_result({})[0] == "处理完成"


def test_build_assistant_message_attaches_results():
    message = chat_messages.build_assistant_message(
        {"results": {"data": [1]}, "message": "ok"}
    )

    assert message["role"] == "assistant"
    assert message["results"] == {"data": [1]}


def test_build_assistant_message_without_results_has_no_results_key():
    message = chat_messages.build_assistant_message({"message": "ok"})

    assert "results" not in message


def test_format_script_confirmation_wraps_script_in_r_code_fence():
    text = chat_messages.format_script_confirmation(
        {"message": "已生成 R 脚本", "script": "library(DESeq2)"}
    )

    assert text == "已生成 R 脚本\n\n```r\nlibrary(DESeq2)\n```"


def test_format_script_confirmation_default_message():
    assert chat_messages.format_script_confirmation({}).startswith(
        "已生成 R 脚本，请审阅并确认执行"
    )


def test_make_llm_summarize_returns_none_without_llm_client():
    assert chat_messages.make_llm_summarize(object()) is None


def test_make_llm_summarize_delegates_to_compact_summarize(monkeypatch):
    from src.control import compact

    seen: dict = {}

    def fake_summarize(client, model, prior, messages):
        seen.update({"model": model, "prior": prior, "messages": messages})
        return "## 目标\n做差异"

    monkeypatch.setattr(compact, "summarize", fake_summarize)
    agent = type("A", (), {"llm_client": object(), "llm_model": "m1"})()

    summarize = chat_messages.make_llm_summarize(agent)
    assert summarize is not None
    assert summarize("旧摘要", [{"role": "user", "content": "问"}]) == "## 目标\n做差异"
    assert seen == {"model": "m1", "prior": "旧摘要",
                    "messages": [{"role": "user", "content": "问"}]}


def test_app_module_reexports_private_aliases():
    """app.py 只改导入：私有名必须仍是同一实现，存量 test_ui_* 依赖它。"""
    from src.control import chat_messages
    from src.ui import app

    assert app._summarize_result is chat_messages.summarize_result
    assert app._build_assistant_message is chat_messages.build_assistant_message
    assert app._format_script_confirmation is chat_messages.format_script_confirmation
    assert app._make_llm_summarize is chat_messages.make_llm_summarize