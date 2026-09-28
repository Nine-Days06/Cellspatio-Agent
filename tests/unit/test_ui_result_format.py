"""_summarize_result 纯函数测试：结果 → (聊天文本, 引用列表)"""


def _make_result(**kwargs):
    base = {"status": "success"}
    base.update(kwargs)
    return base


def test_knowledge_response_with_references():
    from src.ui.app import _summarize_result

    result = _make_result(
        type="knowledge_response",
        response="TP53 是抑癌基因",
        references=[{"url": "pubmed:123", "title": "TP53 review"}],
        explanation="综合知识库回答",
    )
    content, refs = _summarize_result(result)
    assert "TP53 是抑癌基因" in content
    assert "**结果解读**" in content
    assert "综合知识库回答" in content
    assert len(refs) == 1
    assert refs[0]["url"] == "pubmed:123"


def test_fetch_result():
    from src.ui.app import _summarize_result

    result = _make_result(
        type="fetch_result",
        asset={"asset_id": "GSE123", "access_path": "/data/GSE123.txt"},
    )
    content, refs = _summarize_result(result)
    assert "已下载 GSE123" in content
    assert "/data/GSE123.txt" in content
    assert refs == []


def test_analysis_result_with_capsule():
    from src.ui.app import _summarize_result

    result = _make_result(
        results={"statistics": {}, "data": [], "charts": []},
        message="DESeq2 差异表达完成",
        capsule_dir="snapshots/run-abc",
    )
    content, refs = _summarize_result(result)
    assert "分析完成: DESeq2 差异表达完成" in content
    assert "snapshots/run-abc" in content
    assert refs == []


def test_analysis_result_without_message():
    from src.ui.app import _summarize_result

    content, refs = _summarize_result(_make_result(results={"data": []}))
    assert "分析完成" in content
    assert refs == []


def test_fallback_message():
    from src.ui.app import _summarize_result

    content, refs = _summarize_result(_make_result(message="处理完成"))
    assert content == "处理完成"
    assert refs == []


def test_fallback_default():
    from src.ui.app import _summarize_result

    content, _ = _summarize_result({})
    assert content == "处理完成"


def test_no_explanation_no_separator():
    from src.ui.app import _summarize_result

    content, _ = _summarize_result(_make_result(message="ok"))
    assert "结果解读" not in content
