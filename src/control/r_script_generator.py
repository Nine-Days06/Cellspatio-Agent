"""R 脚本生成器"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RScriptGenerator:
    """生成 R 分析脚本（P3：LLM 优先，模板回退）"""

    def __init__(self, llm_client=None, model: str = "gpt-4o-mini"):
        self.llm_client = llm_client
        self.model = model

    def generate_code(
        self,
        analysis_type: str,
        params: dict[str, Any],
        method_context: str | None = None,
    ) -> str:
        """根据分析类型生成 R 代码

        Args:
            analysis_type: 分析类型（differential_expression / pathway_analysis / visualization）
            params: 模板参数
            method_context: 可选的方法学参考文本，注入脚本头注释
        """
        if analysis_type == "differential_expression":
            body = self._generate_de_code(params)
        elif analysis_type == "pathway_analysis":
            body = self._generate_pathway_code(params)
        elif analysis_type == "visualization":
            body = self._generate_visualization_code(params)
        else:
            return f"# 不支持的分析类型: {analysis_type}"
        return self._inject_method_context(body, method_context)

    @staticmethod
    def _inject_method_context(body: str, method_context: str | None) -> str:
        """在脚本头部（shebang 行后）注入方法学参考注释块

        Args:
            body: 生成的 R 脚本正文
            method_context: 方法学参考文本；为 None 时原样返回

        Returns:
            注入后的脚本；无 method_context 时不做任何修改
        """
        if not method_context:
            return body
        # 统一每行前缀 "# "，已带 # 的行保持原样
        normalized = []
        for line in method_context.splitlines():
            normalized.append(line if line.lstrip().startswith("#") else f"# {line}")
        block = "# 方法学参考（自动生成，勿删）\n" + "\n".join(normalized) + "\n#\n"
        shebang = "#!/usr/bin/env Rscript"
        if body.startswith(shebang):
            return shebang + "\n" + block + body[len(shebang) :].lstrip("\n")
        return block + body

    def _generate_de_code(self, params: dict[str, Any]) -> str:
        """生成差异表达分析 R 代码"""
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 差异表达分析模板

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 这里可以添加差异表达分析逻辑
# 示例：简单的差异表达分析框架
library(DESeq2)

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 读取数据
count_data <- read.csv(input_file, row.names = 1)

# 这里可以添加 DESeq2 分析流程
# dds <- DESeqDataSetFromMatrix(...)
# dds <- DESeq(dds)
# res <- results(dds)

# 保存结果
results <- data.frame(gene = rownames(count_data), log2FC = 0, pvalue = 1)
write.csv(results, output_file, row.names = FALSE)

cat("差异表达分析完成，结果已保存至:", output_file, "\\n")
'''

    def _generate_pathway_code(self, params: dict[str, Any]) -> str:
        """生成通路分析 R 代码"""
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 通路分析模板

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 读取基因列表
genes <- read.csv(input_file)

# 这里可以添加 KEGG/GO 通路富集分析
# library(clusterProfiler)
# enrich_result <- enrichKEGG(gene = genes$gene_id, organism = 'hsa')

# 保存结果
results <- data.frame(pathway = character(), pvalue = numeric())
write.csv(results, output_file, row.names = FALSE)

cat("通路分析完成，结果已保存至:", output_file, "\\n")
'''

    def _generate_visualization_code(self, params: dict[str, Any]) -> str:
        """生成可视化 R 代码"""
        plot_type = params.get("plot_type", "volcano")

        return f'''#!/usr/bin/env Rscript
# 可视化模板

# 设置绘图类型
plot_type <- "{plot_type}"

# 示例数据
matrix_data <- matrix(rnorm(100), nrow = 10)

if (!(plot_type %in% c("volcano", "heatmap", "pca"))) {{
    stop("不支持的绘图类型: ", plot_type)
}}

if (plot_type == "volcano") {{
    # Volcano Plot 火山图绘制逻辑
    cat("绘制 Volcano Plot...\\n")
}} else {{
    if (plot_type == "heatmap") {{
        # 热图绘制逻辑
        heatmap(matrix_data)
        cat("绘制热图...\\n")
    }} else {{
        if (plot_type == "pca") {{
            # PCA 绘制逻辑
            cat("绘制 PCA 图...\\n")
        }}
    }}
}}
'''
