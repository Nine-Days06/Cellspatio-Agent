#!/usr/bin/env Rscript

# 可视化脚本（火山图 / 热图）
args <- commandArgs(trailingOnly=TRUE)

if (length(args) < 2) {
  cat("Usage: Rscript visualization.R <input_file> <output_file> [plot_type]\n")
  cat("  plot_type: volcano (default) or heatmap\n")
  quit(status = 1)
}

input_file <- args[1]
output_file <- args[2]
plot_type <- ifelse(length(args) >= 3, args[3], "volcano")

# 校验 plot_type
if (!(plot_type %in% c("volcano", "heatmap"))) {
  stop("Invalid plot_type '", plot_type, "'. Expected 'volcano' or 'heatmap'")
}

# 读取数据
data <- read.csv(input_file)

if (plot_type == "volcano") {
  # 校验必需列
  if (!("log2FC" %in% colnames(data))) {
    stop("Column 'log2FC' not found in input file")
  }
  if (!("padj" %in% colnames(data))) {
    stop("Column 'padj' not found in input file")
  }

  # 剔除缺失值，显著点（padj < 0.05）标红，其余灰色
  volcano_data <- data[!is.na(data$log2FC) & !is.na(data$padj), ]
  colors <- ifelse(volcano_data$padj < 0.05, "red", "gray")

  # 生成火山图 PNG
  png(output_file)
  plot(volcano_data$log2FC, -log10(volcano_data$padj),
       col = colors, pch = 20,
       xlab = "log2 Fold Change",
       ylab = "-log10(Adjusted P-value)",
       main = "Volcano Plot")
  abline(h = -log10(0.05), lty = 2, col = "blue")
  dev.off()

  cat("Plot type: volcano\n")
  cat("Total points:", nrow(volcano_data), "\n")
  cat("Significant points (padj < 0.05):", sum(volcano_data$padj < 0.05), "\n")
} else {
  # 第一列为标识符时转为行名，其余列转为数值矩阵
  if (is.character(data[[1]]) || is.factor(data[[1]])) {
    row_labels <- data[[1]]
    matrix_data <- as.matrix(data[, -1, drop = FALSE])
    rownames(matrix_data) <- row_labels
  } else {
    matrix_data <- as.matrix(data)
  }
  storage.mode(matrix_data) <- "numeric"

  # 生成热图 PNG
  png(output_file, width = 800, height = 600)
  heatmap(matrix_data, col = heat.colors(100))
  dev.off()

  cat("Plot type: heatmap\n")
  cat("Rows:", nrow(matrix_data), "\n")
  cat("Columns:", ncol(matrix_data), "\n")
}

cat("Saved to:", output_file, "\n")