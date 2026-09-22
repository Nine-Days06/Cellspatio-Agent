# edgeR 差异表达分析

## 适用
- bulk RNA-seq 原始计数；小样本（含 n=2/组时经验贝叶斯更稳）
- 需要 `glmQLFTest` 的准似然检验流程

## 不适用
- 已归一化的表达量矩阵
- 需求复杂设计但不想手写 GLM 公式的场景（可考虑 limma-voom 或 DESeq2 的自动化 workflow）

## 推荐参数
- `filterByExpr(y, design)` 过滤低表达
- `calcNormFactors(y, method="TMM")` 归一化
- QL 流程：`estimateDisp` → `glmQLFit` → `glmQLFTest`
- FDR：`topTags(..., n=Inf)` 取 `FDR < 0.05`

## 常见坑
- 未 filter 低表达基因会干扰归一化因子估计、离散度估计与多重检验，建议先 filterByExpr 再 calcNormFactors
- 把 TMM 后的 CPM 当 counts 输入 GLM 是错的
- design 矩阵与 `group` 因子水平不一致会报错

## R 函数
- `edgeR::filterByExpr`
- `edgeR::calcNormFactors`
- `edgeR::estimateDisp` / `glmQLFit` / `glmQLFTest`
