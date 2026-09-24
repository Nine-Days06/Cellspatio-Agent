import logging
import math
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import seaborn as sns

logger = logging.getLogger(__name__)

class Visualizer:
    """可视化模块，生成图表"""
    
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._set_matplotlib_style()
    
    def _set_matplotlib_style(self):
        """设置 matplotlib 样式，兼容不同版本"""
        try:
            # 尝试使用新版样式
            plt.style.use('seaborn-v0_8')
        except OSError:
            try:
                # 尝试使用旧版样式
                plt.style.use('seaborn')
            except OSError:
                # 如果都找不到，使用默认样式
                logger.warning("Could not find seaborn styles, using default matplotlib style")
    
    def plot_volcano(self, data: pd.DataFrame, 
                    log2fc_col: str = 'log2FC', 
                    pval_col: str = 'padj',
                    title: str = 'Volcano Plot') -> plt.Figure:
        """绘制火山图"""
        fig, ax = plt.subplots(figsize=(10, 8))
        
        # 简化实现
        significant = data[data[pval_col] < 0.05]
        non_significant = data[data[pval_col] >= 0.05]
        
        ax.scatter(non_significant[log2fc_col], -np.log10(non_significant[pval_col]), 
                  alpha=0.5, color='gray', label='Non-significant')
        ax.scatter(significant[log2fc_col], -np.log10(significant[pval_col]), 
                  alpha=0.7, color='red', label='Significant')
        
        ax.set_xlabel('Log2 Fold Change')
        ax.set_ylabel('-Log10 Adjusted P-value')
        ax.set_title(title)
        ax.legend()
        
        return fig
    
    def plot_heatmap(self, data: pd.DataFrame, 
                    title: str = 'Heatmap') -> plt.Figure:
        """绘制热图"""
        fig, ax = plt.subplots(figsize=(12, 8))
        sns.heatmap(data, ax=ax, cmap='viridis')
        ax.set_title(title)
        return fig
    
    def plot_pathway(self, pathway_data: dict[str, Any]) -> plt.Figure:
        """绘制通路图（简化版）"""
        # 实际需要更复杂的通路可视化
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.text(0.5, 0.5, 'Pathway Visualization\n(Placeholder)', 
                ha='center', va='center', fontsize=14)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        return fig

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