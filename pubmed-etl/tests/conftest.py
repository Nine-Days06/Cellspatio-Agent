"""pubmed-etl 测试路径配置：集中管理 sys.path bootstrap，避免测试文件重复设置。

pubmed-etl/ 不是已安装的 Python 包，测试模块通过绝对名称导入项目代码
（如 from cleaner.llm_validator import ...），因此 pubmed-etl/ 目录必须位于 sys.path。

将此配置集中在此处，确保：
1. 每个测试文件都能独立运行
2. 测试结果不依赖于文件收集顺序
3. 路径设置只在一处维护，避免重复
"""

import sys
from pathlib import Path

# 获取 pubmed-etl/ 目录（tests/ 的父目录）
pubmed_etl_dir = Path(__file__).resolve().parents[1]

# 确保 pubmed-etl/ 在 sys.path 中，且不重复添加
if str(pubmed_etl_dir) not in sys.path:
    sys.path.insert(0, str(pubmed_etl_dir))