#!/usr/bin/env Rscript
# 独立副本，仅供手动 Rscript 执行；主项目分析逻辑在 src/control/r_script_generator.py。

# 差异表达分析脚本
args <- commandArgs(trailingOnly=TRUE)

if (length(args) < 2) {
  cat("Usage: Rscript differential_expression.R <input_file> <output_file> [condition_col]\n")
  quit(status = 1)
}

input_file <- args[1]
output_file <- args[2]
condition_col <- ifelse(length(args) >= 3, args[3], "condition")

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 简单的差异表达分析（实际应使用 DESeq2 或 edgeR）
# 这里只是一个示例框架
results <- data.frame(
  gene = rownames(data),
  log2FC = rnorm(nrow(data)),
  pvalue = runif(nrow(data)),
  padj = runif(nrow(data))
)

# 过滤显著差异基因
significant <- results[results$padj < 0.05, ]

# 保存结果
write.csv(results, output_file, row.names = FALSE)

cat("Differential expression analysis completed.\n")
cat("Total genes:", nrow(results), "\n")
cat("Significant genes (padj < 0.05):", nrow(significant), "\n")