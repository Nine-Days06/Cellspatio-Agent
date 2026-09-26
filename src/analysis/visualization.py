import logging
import math
from typing import Any

import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger(__name__)

class Visualizer:
    """可视化模块，生成交互式图表"""

    def plot_volcano_plotly(
        self,
        data: "pd.DataFrame",
        log2fc_col: str = "log2FC",
        pval_col: str = "padj",
        gene_col: str = "gene",
        title: str = "Volcano Plot",
    ):
        """交互式火山图；padj=0 夹到最小正值避免 Inf"""

        df = data.copy()
        if pval_col not in df.columns:
            raise ValueError(f"缺少 {pval_col} 列")
        df = df.dropna(subset=[log2fc_col, pval_col])
        p = df[pval_col].astype(float).clip(lower=1e-300)
        df["neglog10"] = -p.map(math.log10)

        sig = df[df[pval_col] < 0.05]
        ns = df[df[pval_col] >= 0.05]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ns[log2fc_col] if len(ns) else None,
            y=ns["neglog10"] if len(ns) else None,
            mode="markers", name="不显著",
            marker={"color": "gray", "size": 6, "opacity": 0.5},
            text=ns.get(gene_col, None) if len(ns) else None,
            hovertemplate="%{text}<br>log2FC=%{x}<br>-log10p=%{y}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=sig[log2fc_col] if len(sig) else None,
            y=sig["neglog10"] if len(sig) else None,
            mode="markers", name="显著",
            marker={"color": "red", "size": 8, "opacity": 0.7},
            text=sig.get(gene_col, None) if len(sig) else None,
            hovertemplate="%{text}<br>log2FC=%{x}<br>-log10p=%{y}<extra></extra>",
        ))
        fig.add_vline(x=1, line_dash="dot", line_color="black")
        fig.add_vline(x=-1, line_dash="dot", line_color="black")
        fig.add_hline(y=-math.log10(0.05), line_dash="dot", line_color="black")
        fig.update_layout(
            title=title, xaxis_title="Log2 Fold Change",
            yaxis_title="-Log10 adjusted p-value",
            template="plotly_white", height=520,
        )
        return fig

    def plot_heatmap_plotly(self, data: "pd.DataFrame", title: str = "Heatmap"):
        """交互式热图（数值列）"""

        numeric = data.select_dtypes("number")
        if numeric.empty:
            raise ValueError("无数值列可绘制热图")
        fig = go.Figure(data=go.Heatmap(
            z=numeric.values,
            x=list(numeric.columns),
            y=list(data.index.astype(str)),
            colorscale="Viridis",
        ))
        fig.update_layout(title=title, template="plotly_white", height=520)
        return fig