"""预设起点元数据（不依赖 Streamlit 运行时）"""


STARTER_PRESETS = [
    {
        "group": "转录组",
        "label": "找 RNA-seq 数据集",
        "prompt": "帮我找人类 肝癌 RNA-seq 数据集",
    },
    {
        "group": "转录组",
        "label": "差异表达分析",
        "prompt": "对已下载数据做差异表达分析",
    },
    {
        "group": "蛋白组",
        "label": "查蛋白功能",
        "prompt": "TP53 蛋白的功能和通路关系是什么？",
    },
    {
        "group": "通路",
        "label": "通路富集解读",
        "prompt": "解释 KEGG 通路富集分析结果怎么看",
    },
    {
        "group": "知识",
        "label": "基因机制问答",
        "prompt": "BRCA1 在乳腺癌中的作用机制是什么？",
    },
]


def test_presets_have_required_keys():
    for p in STARTER_PRESETS:
        assert set(p) >= {"group", "label", "prompt"}
        assert p["prompt"].strip()


def test_presets_cover_multiple_groups():
    groups = {p["group"] for p in STARTER_PRESETS}
    assert len(groups) >= 3
