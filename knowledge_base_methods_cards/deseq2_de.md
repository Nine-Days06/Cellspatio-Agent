# DESeq2 差异表达分析

## 适用
- bulk RNA-seq 原始计数矩阵（整数 counts）
- 样本数 ≥ 3/组 的两组或多组设计
- 需要收缩估计（shrinkage）的 log2FC 与 FDR 校正

## 不适用
- TPM/FPKM/CPM 等归一化表达量（DESeq2 要求原始 counts）
- 单细胞 UMI 矩阵（应使用 scRNA-seq 专用方法）
- 样本数 < 3 的组（离散度估计不可靠）

## 推荐参数
- 设计公式：`~ batch + condition`，兴趣变量 condition 放在公式最后
- 显著性：`padj < 0.05` 且 `|log2FC| > 1`
- `lfcShrink(type="apeglm")` 收缩 log2FC 后再做排序与可视化

## 常见坑
- 输入不是整数 counts 会直接报错或结果无效
- `results()` 的 `contrast` 对 levels 顺序敏感，写反则 FC 符号相反
- 全零行需在建 `DESeqDataSet` 前过滤
- 不要对 counts 先做 `log2` 或 TPM 转换

## R 函数
- `DESeq2::DESeqDataSetFromMatrix(countData, colData, design)`
- `DESeq2::DESeq(dds)`
- `DESeq2::results(dds, alpha=0.05)`
- `DESeq2::lfcShrink(dds, coef=..., type="apeglm")`
