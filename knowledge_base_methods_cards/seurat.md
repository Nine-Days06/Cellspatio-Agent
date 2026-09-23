# Seurat 单细胞分析

## 适用
- 人类 scRNA-seq 整数 counts 矩阵（基因 × 细胞）
- 需要 QC、降维聚类、UMAP、marker 基因

## 不适用
- bulk 样本级矩阵（应用 DESeq2 等 bulk 方法）
- 已高度定制的 Space Ranger 以外的空间目录（空间见 spatial_visium 卡）

## 推荐参数
- min.features = 200，max nFeature 约 5000，percent.mt < 20（实体组织可放宽）
- FindVariableFeatures nfeatures = 2000
- resolution：0.2–1.0，初探 0.5；类群过碎调低、过粗调高
- FindAllMarkers logfc.threshold = 0.25，only.pos = TRUE

## 常见坑
- 输入若是 TPM/CPM 而非 counts，NormalizeData 前应确认量纲
- MT 基因前缀人源 `MT-`、小鼠 `mt-`，模式需匹配物种
- 细胞数 < 10 时 PCA/UMAP 不稳定，先查矩阵是否读反（基因×细胞）
- resolution 不影响生物学真理，聚类需结合 marker 解释

## R 函数
- `Seurat::CreateSeuratObject(counts, min.cells, min.features)`
- `Seurat::NormalizeData` / `FindVariableFeatures` / `ScaleData` / `RunPCA`
- `Seurat::FindNeighbors` / `FindClusters` / `RunUMAP`
- `Seurat::FindAllMarkers`