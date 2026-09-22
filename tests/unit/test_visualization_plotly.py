"""plotly 可视化单测"""
import pandas as pd
import pytest

plotly = pytest.importorskip("plotly")


def test_plot_volcano_plotly_returns_figure():
    from src.analysis.visualization import Visualizer
    df = pd.DataFrame({
        "log2FC": [-3.0, 0.1, 2.5],
        "padj": [0.001, 0.5, 0.01],
        "gene": ["A", "B", "C"],
    })
    fig = Visualizer().plot_volcano_plotly(df)
    assert fig.data  # 含 trace
    # 至少两组点（显著/不显著）
    assert len(fig.data) >= 2


def test_plot_volcano_plotly_handles_zero_pval():
    from src.analysis.visualization import Visualizer
    df = pd.DataFrame({"log2FC": [0.0], "padj": [0.0], "gene": ["Z"]})
    fig = Visualizer().plot_volcano_plotly(df)
    ys = [y for tr in fig.data for y in (tr.y or [])]
    assert all(y != float("inf") for y in ys)
