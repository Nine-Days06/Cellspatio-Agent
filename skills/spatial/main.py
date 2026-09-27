"""空间转录组分析编排器技能：校验输入并产出静态分析方案（无 I/O）。"""
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata

REQUIRED_INPUTS = ["空间转录组表达矩阵（spots × genes）", "组织切片图像与坐标"]
PLAN = [
    "Spot 质控与标准化",
    "空间可变基因与空间域识别",
    "空间邻域分析与细胞通讯推断",
    "组织切片叠加可视化",
]


class SpatialSkill(SkillBase):
    metadata = SkillMetadata(
        name="spatial",
        version="1.0.0",
        description="空间转录组分析编排：校验输入并产出分析方案",
        author="CellSpatio",
        tags=["analysis", "spatial"],
    )

    async def execute(self, context: SkillContext) -> dict:
        user_input = context.params.get("user_input", "").strip()
        if not user_input:
            raise ValueError("user_input 不能为空")
        return {
            "analysis_type": "spatial",
            "user_input": user_input,
            "required_inputs": REQUIRED_INPUTS,
            "plan": PLAN,
            "best_practices": context.best_practices,
        }
