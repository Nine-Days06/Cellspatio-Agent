"""差异表达分析编排器技能：校验输入并产出静态分析方案（无 I/O）。"""
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata

REQUIRED_INPUTS = ["表达矩阵（counts/TPM）", "样本分组与设计公式"]
PLAN = [
    "数据质控与标准化（过滤低表达基因）",
    "DESeq2/edgeR 差异检验（|log2FC|≥1, adj.P<0.05）",
    "差异基因火山图与热图可视化",
    "结果导出 de_results.csv",
]


class DifferentialExpressionSkill(SkillBase):
    metadata = SkillMetadata(
        name="differential_expression",
        version="1.0.0",
        description="bulk 差异表达分析编排：校验输入并产出分析方案",
        author="CellSpatio",
        tags=["analysis", "differential-expression"],
    )

    async def execute(self, context: SkillContext) -> dict:
        user_input = context.params.get("user_input", "").strip()
        if not user_input:
            raise ValueError("user_input 不能为空")
        return {
            "analysis_type": "differential_expression",
            "user_input": user_input,
            "required_inputs": REQUIRED_INPUTS,
            "plan": PLAN,
            "best_practices": context.best_practices,
        }
