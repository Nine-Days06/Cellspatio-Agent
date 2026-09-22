# edgeR 差异表达分析

## 适用
- bulk RNA-seq 原始计数；小样本（含 n=2/组 时经验贝叶斯更稳）
- 需要 `glmQLFTest` 的准似然检验流程

## 不适用
- 已归一化的表达量矩阵
- 需要自带 size factor 以外复杂设计且不愿写 GLM 的场景（可改 DESeq2）

## 推荐参数
- `filterByExpr(y, design)` 过滤低表达
- `calcNormFactors(y, method="TMM")` 归一化
- QL 流程：`estimateDisp` → `glmQLFit` → `glmQLFTest`
- FDR：`topTags(..., n=Inf)` 取 `FDR < 0.05`

## 常见坑
- 未 filter 就 calcNormFactors 会浪费自由度
- 把 TMM 后的 CPM 当 counts 输入 GLM 是错的
- design 矩阵与 `group` 因子水平不一致会报错

## R 函数
- `edgeR::filterByExpr`
- `edgeR::calcNormFactors`
- `edgeR::estimateDisp` / `glmQLFit` / `glmQLFTest`
