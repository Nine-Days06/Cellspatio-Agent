# 批次效应校正

## 适用
- 设计矩阵中含已知批次变量（测序日期、建库批次）
- DESeq2/edgeR 设计公式纳入 batch，或下游用 `removeBatchEffect` 仅用于可视化

## 不适用
- 批次与处理完全混杂（无法统计分离，需重做实验设计）
- 把校正后的值再喂回 DESeq2 当 counts

## 推荐参数
- 差异分析：设计公式 `~ batch + condition`，在模型中校正
- 仅可视化/聚类前：`limma::removeBatchEffect(x, batch=...)`
- 校正后矩阵禁止再作为 DESeq2 输入

## 常见坑
- 先 removeBatchEffect 再 DESeq2 是错误流程
- 批次列写成字符未转 factor，R 会拟合错误
- 样本数少且批次多时模型自由度不足

## R 函数
- `limma::removeBatchEffect`
- DESeq2 `design = ~ batch + condition`
