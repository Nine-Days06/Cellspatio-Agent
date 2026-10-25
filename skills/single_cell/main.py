"""单细胞过滤前 QC 静态方案（无 I/O，不批准下游步骤）。"""

from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata

REQUIRED_INPUTS = ["人类原始 RNA counts（10x MTX 三件套目录或 CSV）", "物种与组织类型"]
PLAN = [
    "计算所有 RNA barcode 的过滤前 counts、检测 feature 数及人类 MT- 比例",
    "保存输入哈希、原 feature 映射、逐细胞指标、汇总与图表到本地",
    "停在 qc_review 等待科学审阅；不执行过滤、聚类或注释",
]


class SingleCellSkill(SkillBase):
    metadata = SkillMetadata(
        name="single_cell",
        version="1.1.0",
        description="单细胞过滤前 QC：固定证据流程，等待科学审阅",
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
