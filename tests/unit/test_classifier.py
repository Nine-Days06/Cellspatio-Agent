"""TaskClassifier：意图 → 模态/技能映射测试。"""
from src.control.classifier import TaskClassifier


def test_classify_analysis():
    clf = TaskClassifier()
    assert clf.classify("分析差异表达基因") == {"modality": "analysis", "skill": "differential_expression"}
    assert clf.classify("单细胞聚类") == {"modality": "analysis", "skill": "single_cell"}
    assert clf.classify("空间转录组分析") == {"modality": "analysis", "skill": "spatial"}


def test_classify_fetch():
    clf = TaskClassifier()
    assert clf.classify("下载 GSE123456") == {"modality": "fetch", "skill": "search_datasets"}
    assert clf.classify("获取 TP53 蛋白信息") == {"modality": "fetch", "skill": "search_datasets"}


def test_classify_knowledge():
    clf = TaskClassifier()
    assert clf.classify("TP53 是什么") == {"modality": "knowledge", "skill": "query_knowledge"}
    assert clf.classify("差异表达为什么用 DESeq2") == {"modality": "knowledge", "skill": "query_knowledge"}


def test_classify_general():
    clf = TaskClassifier()
    result = clf.classify("你好")
    assert result["modality"] == "general"
    assert result["skill"] is None