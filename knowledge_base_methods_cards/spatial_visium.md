# Visium 空间转录组分析（Seurat）

## 适用
- 10x Space Ranger 输出目录（含 spatial/ 与 filtered_feature_bc_matrix/）
- 需要空间聚类与位置染色图

## 不适用
- 非 10x Visium 的 MERFISH/Slide-seq 原始格式（需专用读入）
- 仅有表达矩阵、无 spatial 点坐标元数据

## 推荐参数
- 与 scRNA 类似：resolution 初探 0.5
- 下游细胞通讯/反卷积不在本卡范围

## 常见坑
- `Load10X_Spatial` 前必须目录结构完整，缺 spatial/tissue_positions 会失败
- 高分辨图像与 scalefactors 不匹配会导致 SpatialPlot 空白
- spot 不是单细胞，解读聚类时避免直接等同细胞类型
- 与 scRNA 联合整合需锚点方法，不在本基础流程内

## R 函数
- `Seurat::Load10X_Spatial(data.dir)`
- `Seurat::SpatialDimPlot` / `SpatialFeaturePlot`
- 标准 Seurat 聚类函数链（同 seurat 卡）