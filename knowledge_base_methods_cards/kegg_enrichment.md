# KEGG 通路富集分析（clusterProfiler）

## 适用
- 已有差异基因 symbol/ENTREZID 列表
- 人类（organism="hsa"）通路背景

## 不适用
- 未做 ID 映射的任意字符串基因名
- 需要自定义 universe 时未提供背景基因集

## 推荐参数
- `enrichKEGG(gene=..., organism="hsa", pAdjustMethod="BH", qValueCutoff=0.05)`
- 输入 gene 通常为 ENTREZID；symbol 先 `bitr` 映射
- 显著条目展示 top 10–20

## 常见坑
- symbol 直接喂 enrichKEGG 会静默得到空结果
- 背景默认是 KEGG 全基因，不等于你的检测基因集；严格分析应传 `universe`
- 多重检验用 BH，不要只看 raw p

## R 函数
- `clusterProfiler::enrichKEGG`
- `clusterProfiler::bitr`
- `enrichplot::dotplot`
