import logging
from typing import Any

logger = logging.getLogger(__name__)

class RScriptGenerator:
    """R 脚本生成器，根据分析类型生成对应的 R 脚本"""

    def __init__(self):
        self.analysis_templates = {
            'differential_expression': self._generate_de_template,
            'pathway_analysis': self._generate_pathway_template,
            'visualization': self._generate_visualization_template,
        }

    def generate_code(self, analysis_type: str, params: dict[str, Any]) -> str:
        """生成分析代码"""
        generator = self.analysis_templates.get(analysis_type)
        if generator:
            return generator(params)
        return f"# 不支持的分析类型: {analysis_type}"

    def _generate_de_template(self, params: dict[str, Any]) -> str:
        """生成差异表达分析模板"""
        input_file = params.get('input_file', 'data.csv')
        output_file = params.get('output_file', 'results.csv')

        return f"""#!/usr/bin/env Rscript

# 差异表达分析
args <- commandArgs(trailingOnly=TRUE)
input_file <- "{input_file}"
output_file <- "{output_file}"

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 差异表达分析（示例框架）
results <- data.frame(
  gene = rownames(data),
  log2FC = rnorm(nrow(data)),
  pvalue = runif(nrow(data)),
  padj = runif(nrow(data))
)

write.csv(results, output_file, row.names = FALSE)
"""

    def _generate_pathway_template(self, params: dict[str, Any]) -> str:
        """生成通路分析模板"""
        return """#!/usr/bin/env Rscript

# 通路富集分析
args <- commandArgs(trailingOnly=TRUE)
input_file <- args[1]
output_file <- args[2]

data <- read.csv(input_file)
results <- data.frame(gene = data$gene, pathway = "KEGG", pvalue = 0.05)
write.csv(results, output_file, row.names = FALSE)
"""

    def _generate_visualization_template(self, params: dict[str, Any]) -> str:
        """生成可视化模板"""
        input_file = params.get('input_file', 'data.csv')
        output_file = params.get('output_file', 'output.png')
        plot_type = params.get('plot_type', 'volcano')

        return f"""#!/usr/bin/env Rscript

# 可视化脚本（火山图 / 热图）
input_file <- "{input_file}"
output_file <- "{output_file}"
plot_type <- "{plot_type}"

if (!(plot_type %in% c("volcano", "heatmap"))) {{
  stop("Invalid plot_type '", plot_type, "'. Expected 'volcano' or 'heatmap'")
}}

data <- read.csv(input_file)

if (plot_type == "volcano") {{
  # 火山图
  png(output_file)
  plot(data$log2FC, -log10(data$padj), pch = 20,
       xlab = "log2 Fold Change", ylab = "-log10(Adjusted P-value)",
       main = "Volcano Plot")
  dev.off()
}} else {{
  # 热图
  matrix_data <- as.matrix(data[, -1])
  png(output_file, width = 800, height = 600)
  heatmap(matrix_data, col = heat.colors(100))
  dev.off()
}}
"""