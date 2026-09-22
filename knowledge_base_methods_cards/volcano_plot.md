# 火山图（Volcano Plot）

## 适用
- 差异表达结果表含 log2FC 与 padj/pvalue 列
- 快速展示上下调基因分布

## 不适用
- 无统计检验列的表达矩阵
- 需要交互式悬停展示基因名时，静态 ggplot2 不足（可使用 plotly::ggplotly 转换或 R plotly 原生绘图）

## 推荐参数
- 阈值线：`|log2FC|=1`，`padj=0.05`
- y 轴用 `-log10(padj)`，p=0 的点先夹到最小正值
- 显著点着色：上调红、下调蓝、不显著灰

## 常见坑
- 直接 `-log10(0)` 得到 Inf，图会坏
- padj 为 NA 的点应剔除而非当不显著
- 用未收缩 log2FC 画图时与 lfcShrink 后排序不一致

## R 函数
- `ggplot2::geom_point`
- `EnhancedVolcano::EnhancedVolcano`
