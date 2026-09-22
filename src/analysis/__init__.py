# 分析层模块
"""分析层：RExecutor 通过 subprocess 调 Rscript，Visualizer 画图，ResultExplainer 解释结果。"""
from src.analysis.result_explainer import ResultExplainer
from src.analysis.r_executor import RExecutor, RExecutorError
from src.analysis.visualization import Visualizer

__all__ = ["RExecutor", "RExecutorError", "ResultExplainer", "Visualizer"]
