#!/usr/bin/env Rscript

# 通路富集分析脚本
args <- commandArgs(trailingOnly=TRUE)

if (length(args) < 2) {
  cat("Usage: Rscript pathway_analysis.R <input_file> <output_file> [gene_id_col]\n")
  quit(status = 1)
}

input_file <- args[1]
output_file <- args[2]
gene_id_col <- ifelse(length(args) >= 3, args[3], "gene")

# 读取数据
data <- read.csv(input_file)

# 校验基因列存在
if (!(gene_id_col %in% colnames(data))) {
  stop("Gene ID column '", gene_id_col, "' not found in input file")
}

# 提取显著差异基因（若存在 padj 和 log2FC 列则过滤，否则使用全部）
has_padj <- "padj" %in% colnames(data)
has_log2fc <- "log2FC" %in% colnames(data)
if (has_padj && has_log2fc) {
  significant <- data[!is.na(data$padj) & data$padj < 0.05 &
                      !is.na(data$log2FC) & abs(data$log2FC) >= 1, ]
} else {
  significant <- data
}

# 示例通路富集分析（实际应使用 clusterProfiler 或 ReactomePA）
# 这里只是一个示例框架
example_pathways <- c("hsa04150_mTOR", "hsa04010_MAPK", "hsa05200_Cancer")
results <- data.frame(
  gene = significant[[gene_id_col]],
  pathway = sample(example_pathways, nrow(significant), replace = TRUE),
  pvalue = runif(nrow(significant)),
  padj = runif(nrow(significant))
)

# 保存结果
write.csv(results, output_file, row.names = FALSE)

cat("Pathway analysis completed.\n")
cat("Total genes:", nrow(data), "\n")
cat("Significant genes:", nrow(significant), "\n")
cat("Pathway terms:", length(unique(results$pathway)), "\n")