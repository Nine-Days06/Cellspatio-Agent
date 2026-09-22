# GO 富集分析（clusterProfiler）

## 适用
- 差异基因列表做 BP/CC/MF 三类富集
- 需要同时输出 ORA（超几何）结果

## 不适用
- 想做 GSEA 排序富集时应用 `gseGO` 而非 `enrichGO`

## 推荐参数
- `enrichGO(gene=..., OrgDb=org.Hs.eg.db, keyType="SYMBOL", ont="BP", readable=TRUE)`
- `pAdjustMethod="BH"`，`qValueCutoff=0.05`

## 常见坑
- `keyType` 必须与 gene 实际 ID 类型一致（SYMBOL vs ENTREZID）
- `ont` 写 "ALL" 会混合三类，解读困难
- 未设 universe 时背景默认全基因组

## R 函数
- `clusterProfiler::enrichGO`
- `clusterProfiler::gseGO`
