"""单细胞聚类分析编排器技能：校验输入并产出静态分析方案（无 I/O）。"""
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata

REQUIRED_INPUTS = ["单细胞表达矩阵（10x mtx/h5ad）", "物种与组织类型"]
PLAN = [
    "质控过滤（nFeature、线粒体比例阈值）",
    "标准化、高变基因选择与 PCA 降维",
    "聚类与 UMAP 可视化（resolution 可调）",
    "细胞类型注释（标记基因 / SingleR）",
]


class SingleCellSkill(SkillBase):
    metadata = SkillMetadata(
        name="single_cell",
        version="1.0.0",
        description="单细胞聚类分析编排：校验输入并产出分析方案",
        author="CellSpatio",
        tags=["analysis", "single-cell"],
    )

    async def execute(self, context: SkillContext) -> dict:
        user_input = context.params.get("user_input", "").strip()
        if not user_input:
            raise ValueError("user_input 不能为空")
        return {
            "analysis_type": "single_cell",
            "user_input": user_input,
            "required_inputs": REQUIRED_INPUTS,
            "plan": PLAN,
            "best_practices": context.best_practices,
        }
