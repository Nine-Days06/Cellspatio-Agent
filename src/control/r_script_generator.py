"""R 脚本生成器"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

ANALYSIS_TYPE_DESC = {
    "differential_expression": "两组样本差异表达分析（输出 gene/log2FC/pvalue/padj 表）",
    "pathway_analysis": "差异基因的 KEGG/GO 通路富集分析",
    "visualization": "按 params['plot_type'] 绘制火山图/热图/PCA 图并保存",
    "single_cell": "单细胞 RNA-seq QC→标准化→降维→聚类，输出每细胞 cluster 与 UMAP 坐标、marker 表",
    "spatial": "空间转录组加载→标准化→聚类→空间位置染色图",
}


class RScriptGenerator:
    """生成 R 分析脚本（LLM 动态生成优先，模板回退）"""

    def __init__(self, llm_client=None, model: str = "gpt-4o-mini"):
        self.llm_client = llm_client
        self.model = model

    def generate_code(
        self,
        analysis_type: str,
        params: dict[str, Any],
        method_context: str | None = None,
    ) -> str:
        """根据分析类型生成 R 代码

        Args:
            analysis_type: 分析类型
            params: 模板参数
            method_context: 可选的方法学参考文本，注入脚本头注释
        """
        if analysis_type not in ANALYSIS_TYPE_DESC:
            return f"# 不支持的分析类型: {analysis_type}"

        body = None
        if self.llm_client:
            try:
                body = self._generate_with_llm(analysis_type, params, method_context)
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM codegen failed, fallback to template: %s", e)
                body = None

        if not body:
            body = self._template_for(analysis_type, params)

        return self._inject_method_context(body, method_context)

    def _template_for(self, analysis_type: str, params: dict[str, Any]) -> str:
        if analysis_type == "differential_expression":
            return self._generate_de_code(params)
        if analysis_type == "pathway_analysis":
            return self._generate_pathway_code(params)
        if analysis_type == "visualization":
            return self._generate_visualization_code(params)
        if analysis_type == "single_cell":
            return self._generate_single_cell_code(params)
        if analysis_type == "spatial":
            return self._generate_spatial_code(params)
        return f"# 不支持的分析类型: {analysis_type}"

    def _generate_with_llm(
        self,
        analysis_type: str,
        params: dict[str, Any],
        method_context: str | None,
    ) -> str | None:
        if not self.llm_client:
            return None
        desc = ANALYSIS_TYPE_DESC.get(analysis_type, analysis_type)
        prompt = (
            "你是 CellSpatio 单细胞与时空组学分析智能体的 R 代码生成器。"
            "请生成完整可执行 R 脚本。\n"
            f"## 分析类型\n{analysis_type}: {desc}\n"
            f"## 参数（必须使用这些文件路径）\n{json.dumps(params, ensure_ascii=False)}\n"
            f"## 方法学参考（必须遵循）\n{method_context or '（无）'}\n"
            "## 要求\n"
            "1. 只输出 R 代码，不要 Markdown 围栏，不要解释\n"
            "2. 首行 #!/usr/bin/env Rscript\n"
            "3. 使用成熟 Bioconductor/CRAN 包完成真实分析，禁止占位结果或随机数假结果\n"
            "4. 结果写入 params 中的 output_file\n"
            "5. 用 cat() 打印关键统计（基因数、显著数、聚类数等）\n"
        )
        resp = self.llm_client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=4000,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text or None

    @staticmethod
    def _inject_method_context(body: str, method_context: str | None) -> str:
        """在脚本头部（shebang 行后）注入方法学参考注释块

        Args:
            body: 生成的 R 脚本正文
            method_context: 方法学参考文本；为 None 时原样返回

        Returns:
            注入后的脚本；无 method_context 时不做任何修改
        """
        if not method_context:
            return body
        # 统一每行前缀 "# "，已带 # 的行保持原样
        normalized = []
        for line in method_context.splitlines():
            normalized.append(line if line.lstrip().startswith("#") else f"# {line}")
        block = "# 方法学参考（自动生成，勿删）\n" + "\n".join(normalized) + "\n#\n"
        shebang = "#!/usr/bin/env Rscript"
        if body.startswith(shebang):
            return shebang + "\n" + block + body[len(shebang) :].lstrip("\n")
        return block + body

    def _generate_de_code(self, params: dict[str, Any]) -> str:
        """真实 DESeq2 差异表达（回退模板）。

        输入约定：counts CSV，行为基因、列为样本。
        分组：params['group_csv'] 指向样本注释 CSV（行名=样本名，一列 group）；
        否则前 floor(n/2) 为 control、其余为 treatment。
        """
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "output.csv")
        group_csv = params.get("group_csv", "")

        if group_csv:
            group_block = (
                f'coldata_df <- read.csv("{group_csv}", row.names = 1, '
                "stringsAsFactors = FALSE)\n"
                "groups <- factor(coldata_df[colnames(counts), 1])\n"
            )
        else:
            group_block = (
                "n <- ncol(counts)\n"
                'groups <- factor(c(rep("control", floor(n / 2)), '
                'rep("treatment", ceiling(n / 2))))\n'
            )

        return f'''#!/usr/bin/env Rscript
# 差异表达分析（DESeq2）
input_file <- "{input_file}"
output_file <- "{output_file}"

if (!requireNamespace("DESeq2", quietly = TRUE)) {{
    stop("请先安装 DESeq2: BiocManager::install('DESeq2')")
}}
library(DESeq2)

counts <- as.matrix(read.csv(input_file, row.names = 1, check.names = FALSE))
counts <- round(counts)
counts <- counts[rowSums(counts) >= 10, , drop = FALSE]
if (nrow(counts) == 0 || ncol(counts) < 4) {{
    stop("counts 矩阵为空或样本数 < 4")
}}

{group_block}
if (any(is.na(groups)) || nlevels(groups) < 2) {{
    stop("分组无效：需要至少两个水平的 factor")
}}
coldata <- data.frame(group = groups, row.names = colnames(counts))

dds <- DESeqDataSetFromMatrix(countData = counts, colData = coldata, design = ~ group)
dds <- DESeq(dds)
res <- as.data.frame(results(
    dds, contrast = c("group", levels(groups)[2], levels(groups)[1])
))
res$gene <- rownames(res)
res <- res[order(res$padj), ]

write.csv(
    res[, c("gene", "log2FoldChange", "lfcSE", "stat", "pvalue", "padj")],
    output_file,
    row.names = FALSE
)
sig <- sum(res$padj < 0.05, na.rm = TRUE)
cat("总基因数:", nrow(res), "显著差异基因(padj<0.05):", sig, "\\n")
cat("结果已保存至:", output_file, "\\n")
'''

    def _generate_pathway_code(self, params: dict[str, Any]) -> str:
        """真实 clusterProfiler KEGG 富集（回退模板）。

        输入约定：DE 输出 CSV（含 gene 与 padj 列）。
        """
        input_file = params.get("input_file", "de_results.csv")
        output_file = params.get("output_file", "pathway_enrichment.csv")

        return f'''#!/usr/bin/env Rscript
# 通路富集分析（clusterProfiler + KEGG）
input_file <- "{input_file}"
output_file <- "{output_file}"

if (!requireNamespace("clusterProfiler", quietly = TRUE)) {{
    stop("请先安装: BiocManager::install('clusterProfiler')")
}}
if (!requireNamespace("org.Hs.eg.db", quietly = TRUE)) {{
    stop("请先安装: BiocManager::install('org.Hs.eg.db')")
}}
library(clusterProfiler)
library(org.Hs.eg.db)

de <- read.csv(input_file, stringsAsFactors = FALSE)
if (!all(c("gene", "padj") %in% colnames(de))) {{
    stop("输入需要 gene 与 padj 列（差异表达输出）")
}}
genes <- de$gene[which(de$padj < 0.05)]
genes <- genes[!is.na(genes)]
if (length(genes) == 0) {{
    write.csv(data.frame(), output_file, row.names = FALSE)
    cat("无显著差异基因，跳过富集\\n")
    quit(save = "no", status = 0)
}}

ek <- enrichKEGG(gene = genes, organism = "hsa", pvalueCutoff = 0.05)
ek_df <- as.data.frame(ek)
write.csv(ek_df, output_file, row.names = FALSE)
cat("富集通路数:", nrow(ek_df), "输入基因数:", length(genes), "\\n")
cat("结果已保存至:", output_file, "\\n")
'''

    def _generate_visualization_code(self, params: dict[str, Any]) -> str:
        """生成可视化 R 代码（真实 base R 绘图：火山图/热图/PCA）。

        输入约定：CSV 文件。
        - volcano: 需 log2FC 与 padj 列。
        - heatmap: 首列为标识符时作行名，其余列为数值矩阵。
        - pca: 数值矩阵，行=样本/特征，列=变量。
        输出：PNG 图片写入 output_file；cat() 打印关键统计。
        """
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "plot.png")
        plot_type = params.get("plot_type", "volcano")

        return f'''#!/usr/bin/env Rscript
# 可视化（base R：火山图 / 热图 / PCA）
input_file <- "{input_file}"
output_file <- "{output_file}"
plot_type <- "{plot_type}"

if (!(plot_type %in% c("volcano", "heatmap", "pca"))) {{
    stop("不支持的绘图类型: ", plot_type)
}}

# 读取数据
data <- read.csv(input_file, stringsAsFactors = FALSE)

if (plot_type == "volcano") {{
    # 火山图：需 log2FC 与 padj 列
    if (!("log2FC" %in% colnames(data))) {{
        stop("Column 'log2FC' not found in input file")
    }}
    if (!("padj" %in% colnames(data))) {{
        stop("Column 'padj' not found in input file")
    }}

    # 剔除缺失值，显著点（padj < 0.05）标红，其余灰色
    volcano_data <- data[!is.na(data$log2FC) & !is.na(data$padj), ]
    if (nrow(volcano_data) == 0) {{
        stop("No valid points after removing NA values in 'log2FC'/'padj'")
    }}
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

    cat("Plot type: volcano\\n")
    cat("Total points:", nrow(volcano_data), "\\n")
    cat("Significant points (padj < 0.05):", sum(volcano_data$padj < 0.05), "\\n")
}} else if (plot_type == "heatmap") {{
    # 热图：第一列为标识符时转为行名，其余列转为数值矩阵
    if (is.character(data[[1]]) || is.factor(data[[1]])) {{
        row_labels <- data[[1]]
        matrix_data <- as.matrix(data[, -1, drop = FALSE])
        rownames(matrix_data) <- row_labels
    }} else {{
        matrix_data <- as.matrix(data)
    }}
    storage.mode(matrix_data) <- "numeric"

    # 校验数据满足热图聚类要求（hclust 需要至少 2 行 2 列，且含非 NA 数值）
    if (nrow(matrix_data) < 2 || ncol(matrix_data) < 2 || all(is.na(matrix_data))) {{
        stop("No numeric data found for heatmap")
    }}

    # 生成热图 PNG
    png(output_file, width = 800, height = 600)
    heatmap(matrix_data, col = heat.colors(100))
    dev.off()

    cat("Plot type: heatmap\\n")
    cat("Rows:", nrow(matrix_data), "\\n")
    cat("Columns:", ncol(matrix_data), "\\n")
}} else {{
    # PCA：数值矩阵，行=观测，列=变量
    if (is.character(data[[1]]) || is.factor(data[[1]])) {{
        row_labels <- data[[1]]
        matrix_data <- as.matrix(data[, -1, drop = FALSE])
        rownames(matrix_data) <- row_labels
    }} else {{
        matrix_data <- as.matrix(data)
    }}
    storage.mode(matrix_data) <- "numeric"

    # 剔除全 NA 列
    matrix_data <- matrix_data[, colSums(!is.na(matrix_data)) > 0, drop = FALSE]
    if (nrow(matrix_data) < 2 || ncol(matrix_data) < 2) {{
        stop("Insufficient numeric data for PCA")
    }}

    # 中心化并计算 PCA
    pca_res <- prcomp(matrix_data, center = TRUE, scale. = TRUE, na.action = na.omit)
    var_exp <- round(summary(pca_res)$importance[2, 1:2] * 100, 1)

    # 绘制 PCA 散点图（PC1 vs PC2）
    png(output_file, width = 800, height = 600)
    plot(pca_res$x[, 1], pca_res$x[, 2],
         pch = 20, col = "steelblue",
         xlab = paste0("PC1 (", var_exp[1], "%)"),
         ylab = paste0("PC2 (", var_exp[2], "%)"),
         main = "PCA Plot")
    text(pca_res$x[, 1], pca_res$x[, 2], labels = rownames(pca_res$x), cex = 0.6, pos = 3)
    dev.off()

    cat("Plot type: pca\\n")
    cat("Observations:", nrow(pca_res$x), "\\n")
    cat("Variables:", ncol(matrix_data), "\\n")
    cat("PC1 variance:", var_exp[1], "%\\n")
    cat("PC2 variance:", var_exp[2], "%\\n")
}}

cat("Saved to:", output_file, "\\n")
'''

    def _generate_single_cell_code(self, params: dict[str, Any]) -> str:
        """Seurat 标准单细胞流程（回退模板）。

        输入约定：表达矩阵 CSV（行为基因、列为细胞，整数 counts）。
        """
        input_file = params.get("input_file", "scrna.csv")
        output_file = params.get("output_file", "sc_clusters.csv")
        marker_file = params.get("marker_file", "sc_markers.csv")
        resolution = params.get("resolution", 0.5)

        return f'''#!/usr/bin/env Rscript
# 单细胞分析（Seurat）
input_file <- "{input_file}"
output_file <- "{output_file}"
marker_file <- "{marker_file}"

if (!requireNamespace("Seurat", quietly = TRUE)) {{
    stop("请先安装 Seurat")
}}
library(Seurat)

mat <- as.matrix(read.csv(input_file, row.names = 1, check.names = FALSE))
mat <- round(mat)
obj <- CreateSeuratObject(counts = mat, min.cells = 3, min.features = 200)
if (ncol(obj) < 10) {{
    stop("质控后细胞数 < 10，请检查输入矩阵")
}}
obj[["percent.mt"]] <- PercentageFeatureSet(obj, pattern = "^MT-|^mt-")
obj <- subset(
    obj,
    subset = nFeature_RNA > 200 & nFeature_RNA < 5000 & percent.mt < 20
)
if (ncol(obj) < 10) {{
    stop("过滤后细胞数 < 10，请放宽 QC 阈值")
}}
obj <- NormalizeData(obj, verbose = FALSE)
obj <- FindVariableFeatures(obj, nfeatures = 2000, verbose = FALSE)
obj <- ScaleData(obj, verbose = FALSE)
npcs <- min(30, ncol(obj) - 1, nrow(obj) - 1)
obj <- RunPCA(obj, npcs = npcs, verbose = FALSE)
obj <- FindNeighbors(obj, dims = 1:min(15, npcs), verbose = FALSE)
obj <- FindClusters(obj, resolution = {resolution}, verbose = FALSE)
obj <- RunUMAP(obj, dims = 1:min(15, npcs), verbose = FALSE)

emb <- Embeddings(obj, "umap")
out <- data.frame(
    cell = rownames(emb),
    umap_1 = emb[, 1],
    umap_2 = emb[, 2],
    cluster = as.character(obj$seurat_clusters),
    nFeature_RNA = obj$nFeature_RNA,
    row.names = NULL
)
write.csv(out, output_file, row.names = FALSE)

markers <- FindAllMarkers(
    obj, only.pos = TRUE, max.cells.per.ident = 300,
    logfc.threshold = 0.25, verbose = FALSE
)
write.csv(markers, marker_file, row.names = FALSE)
cat("细胞数:", ncol(obj),
    "聚类数:", length(levels(obj$seurat_clusters)), "\\n")
cat("cluster 表:", output_file, "marker 表:", marker_file, "\\n")
'''

    def _generate_spatial_code(self, params: dict[str, Any]) -> str:
        """Seurat Visium 空间流程（回退模板）。

        输入约定：spaceranger 输出目录（含 spatial/ 与 filtered_feature_bc_matrix/）。
        """
        input_file = params.get("input_file", "spatial_dir")
        output_file = params.get("output_file", "spatial_clusters.csv")
        plot_file = params.get("plot_file", "spatial_plot.png")
        resolution = params.get("resolution", 0.5)

        return f'''#!/usr/bin/env Rscript
# 空间转录组分析（Seurat Visium）
input_dir <- "{input_file}"
output_file <- "{output_file}"
plot_file <- "{plot_file}"

if (!requireNamespace("Seurat", quietly = TRUE)) {{
    stop("请先安装 Seurat（>= 4.1 含空间接口）")
}}
library(Seurat)

if (!dir.exists(input_dir)) {{
    stop("输入目录不存在: ", input_dir)
}}
obj <- Load10X_Spatial(data.dir = input_dir)
obj <- NormalizeData(obj, verbose = FALSE)
obj <- FindVariableFeatures(obj, nfeatures = 2000, verbose = FALSE)
obj <- ScaleData(obj, verbose = FALSE)
obj <- RunPCA(obj, npcs = 30, verbose = FALSE)
obj <- FindNeighbors(obj, dims = 1:15, verbose = FALSE)
obj <- FindClusters(obj, resolution = {resolution}, verbose = FALSE)
obj <- RunUMAP(obj, dims = 1:15, verbose = FALSE)

coords <- GetTissueCoordinates(obj)
out <- data.frame(
    spot = rownames(coords),
    x = coords[, 1],
    y = coords[, 2],
    cluster = as.character(obj$seurat_clusters),
    row.names = NULL
)
write.csv(out, output_file, row.names = FALSE)

png(plot_file, width = 1400, height = 1200, res = 150)
print(SpatialDimPlot(obj, label = TRUE, label.size = 3) + NoLegend())
dev.off()
cat("spot 数:", ncol(obj),
    "聚类数:", length(levels(obj$seurat_clusters)), "\\n")
cat("cluster 表:", output_file, "空间图:", plot_file, "\\n")
'''