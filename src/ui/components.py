from typing import Any

import pandas as pd
import streamlit as st

STARTER_PRESETS: list[dict[str, str]] = [
    {"group": "转录组", "label": "找 RNA-seq 数据集",
     "prompt": "帮我找人类 肝癌 RNA-seq 数据集"},
    {"group": "转录组", "label": "差异表达分析",
     "prompt": "对已下载数据做差异表达分析"},
    {"group": "蛋白组", "label": "查蛋白功能",
     "prompt": "TP53 蛋白的功能和通路关系是什么？"},
    {"group": "通路", "label": "通路富集解读",
     "prompt": "解释 KEGG 通路富集分析结果怎么看"},
    {"group": "知识", "label": "基因机制问答",
     "prompt": "BRCA1 在乳腺癌中的作用机制是什么？"},
    {"group": "单细胞", "label": "单细胞聚类 UMAP",
     "prompt": "对已下载的单细胞表达矩阵做聚类和 UMAP"},
    {"group": "时空", "label": "空间转录组",
     "prompt": "分析 Visium 空间转录组数据并输出空间聚类图"},
]


def render_starter_presets() -> str | None:
    """空会话时渲染分组预设按钮；返回被点击的 prompt，否则 None"""
    import streamlit as st

    st.markdown("#### 不知道从哪开始？试试这些")
    groups: dict[str, list[dict[str, str]]] = {}
    for p in STARTER_PRESETS:
        groups.setdefault(p["group"], []).append(p)

    clicked = None
    for group_name, items in groups.items():
        cols = st.columns(len(items))
        for col, item in zip(cols, items):
            with col:
                if st.button(item["label"], key=f"preset_{item['label']}",
                             use_container_width=True):
                    clicked = item["prompt"]
    return clicked


def render_file_uploader(accepted_types: list | None = None) -> str:
    """渲染文件上传组件"""
    if accepted_types is None:
        accepted_types = ["csv", "tsv", "fastq", "vcf", "fasta"]
    
    uploaded_file = st.file_uploader(
        "上传数据文件",
        type=accepted_types,
        help="支持的格式: " + ", ".join(accepted_types)
    )
    
    if uploaded_file is not None:
        return uploaded_file.name
    return None

def render_analysis_results(results: dict[str, Any]):
    """渲染分析结果"""
    if not results:
        st.warning("没有可显示的结果")
        return
    
    # 显示统计信息
    if 'statistics' in results:
        st.subheader("统计摘要")
        st.json(results['statistics'])
    
    # 显示数据表格
    if 'data' in results:
        st.subheader("详细数据")
        df = pd.DataFrame(results['data'])
        st.dataframe(df)
    
    # 显示图表
    if "charts" in results:
        st.subheader("可视化图表")
        for chart in results["charts"]:
            # plotly Figure
            if hasattr(chart, "to_dict") and not hasattr(chart, "savefig"):
                st.plotly_chart(chart, use_container_width=True)
            # matplotlib Figure
            elif hasattr(chart, "savefig"):
                st.pyplot(chart)
            # plotly fig dict（未来）
            elif isinstance(chart, dict) and chart.get("data"):
                st.plotly_chart(chart, use_container_width=True)
            else:
                st.warning("未知图表类型，已跳过")

    render_gene_followup(results)


def gene_followup_prompt(gene: str) -> str:
    """基因 → 知识库追问 prompt"""
    g = (gene or "").strip()
    if not g:
        return ""
    return f"{g} 的功能、通路关系和研究意义是什么？"


def render_gene_followup(results: dict[str, Any]) -> None:
    """结果表含 gene 列时，提供选择并写入 auto_prompt"""
    import streamlit as st

    data = results.get("data")
    if not data:
        return
    try:
        df = pd.DataFrame(data)
    except Exception:  # noqa: BLE001
        return
    if "gene" not in df.columns:
        return
    genes = [str(g) for g in df["gene"].dropna().astype(str).head(50)]
    if not genes:
        return
    st.markdown("#### 追问知识库")
    gene = st.selectbox("选择基因", genes, key="gene_followup_select")
    if st.button("查询该基因", key="gene_followup_btn"):
        st.session_state.auto_prompt = gene_followup_prompt(gene)


def render_knowledge_response(response: str):
    """渲染知识查询响应"""
    st.markdown("### 知识查询结果")
    st.markdown(response)
    
    # 添加反馈按钮
    col1, col2 = st.columns(2)
    with col1:
        if st.button("有帮助"):
            st.success("感谢您的反馈！")
    with col2:
        if st.button("需要改进"):
            st.info("我们会持续改进知识库。")
