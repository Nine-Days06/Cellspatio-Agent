"""R 脚本生成器"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _r_string(value: Any) -> str:
    """把 Python 字符串写成安全的 R 字面量（Windows 反斜杠不能直接进双引号）。"""
    return json.dumps(str(value), ensure_ascii=False)


ANALYSIS_TYPE_DESC = {
    "differential_expression": "两组样本差异表达分析（输出 gene/log2FC/pvalue/padj 表）",
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
        input_code = (
            self._single_cell_input_code(params) if analysis_type == "single_cell" else None
        )
        if input_code:
            prompt += (
                "6. 必须原样包含以下输入读取与校验代码，后续从 mat 创建 Seurat 对象；"
                "不得替换读取方式、转成稠密矩阵或四舍五入 counts。\n" + input_code
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
        if input_code and input_code not in text:
            logger.warning("LLM single-cell code omitted the validated input block; using template")
            return None
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
        input_file = _r_string(params.get("input_file", "input.csv"))
        output_file = _r_string(params.get("output_file", "output.csv"))
        group_csv = _r_string(params.get("group_csv", ""))

        if params.get("group_csv"):
            group_block = (
                f"coldata_df <- read.csv({group_csv}, row.names = 1, "
                "stringsAsFactors = FALSE)\n"
                "groups <- factor(coldata_df[colnames(counts), 1])\n"
            )
        else:
            group_block = (
                "n <- ncol(counts)\n"
                'groups <- factor(c(rep("control", floor(n / 2)), '
                'rep("treatment", ceiling(n / 2))))\n'
            )

        return f"""#!/usr/bin/env Rscript
# 差异表达分析（DESeq2）
input_file <- {input_file}
output_file <- {output_file}

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
"""

    @staticmethod
    def _single_cell_input_code(params: dict[str, Any]) -> str:
        """模板与 LLM 共用 Seurat 输入块，稀疏 counts 仅检查存储值。"""
        input_file = json.dumps(str(params.get("input_file", "scrna.csv")), ensure_ascii=False)
        return f"""input_file <- {input_file}
if (!requireNamespace("Seurat", quietly = TRUE)) {{
    stop("请先安装 Seurat")
}}
library(Seurat)
if (dir.exists(input_file)) {{
    pick_file <- function(names) {{
        paths <- file.path(input_file, c(names, paste0(names, ".gz")))
        paths <- paths[file.exists(paths) & !dir.exists(paths)]
        if (length(paths) != 1) stop("10x 三件套缺失或有歧义")
        paths[[1]]
    }}
    matrix_file <- pick_file("matrix.mtx")
    barcode_file <- pick_file("barcodes.tsv")
    feature_file <- pick_file(c("features.tsv", "genes.tsv"))
    features <- read.delim(feature_file, header = FALSE, colClasses = "character",
                           quote = "", comment.char = "")
    legacy <- grepl("^genes\\\\.tsv", basename(feature_file))
    if (ncol(features) != (if (legacy) 2 else 3)) stop("feature 列数不符")
    rna <- if (legacy) rep(TRUE, nrow(features)) else features[[3]] == "Gene Expression"
    if (!any(rna)) stop("10x 数据不包含 Gene Expression")
    standard_v2 <- legacy && basename(feature_file) == "genes.tsv" &&
                   basename(barcode_file) == "barcodes.tsv" && basename(matrix_file) == "matrix.mtx"
    standard_v3 <- basename(feature_file) == "features.tsv.gz" &&
                   basename(barcode_file) == "barcodes.tsv.gz" && basename(matrix_file) == "matrix.mtx.gz"
    if (standard_v2 || standard_v3) {{
        mat <- Read10X(data.dir = input_file, unique.features = TRUE)
        if (is.list(mat)) mat <- mat[["Gene Expression"]]
    }} else {{
        mat <- ReadMtx(mtx = matrix_file, cells = barcode_file,
                       features = feature_file, unique.features = TRUE)
        mat <- mat[rna, , drop = FALSE]
    }}
}} else {{
    mat <- as.matrix(read.csv(input_file, row.names = 1, check.names = FALSE))
}}
# 稀疏矩阵的隐式零合法，只检查存储值，避免整块展开。
counts <- if (inherits(mat, "sparseMatrix")) mat@x else mat
if (nrow(mat) == 0 || ncol(mat) == 0 || !is.numeric(counts) ||
    any(!is.finite(counts)) || any(counts < 0) || any(counts != floor(counts))) {{
    stop("输入必须是非空的原始 counts 矩阵：有限、非负整数，不能缺失或四舍五入")
}}
"""

    def _generate_single_cell_code(self, params: dict[str, Any]) -> str:
        """Seurat 标准单细胞流程（回退模板）。

        输入约定：10x MTX 目录或 CSV（行为基因、列为细胞，整数 counts）。
        """
        output_file = json.dumps(
            str(params.get("output_file", "sc_clusters.csv")), ensure_ascii=False
        )
        marker_file = json.dumps(
            str(params.get("marker_file", "sc_markers.csv")), ensure_ascii=False
        )
        resolution = params.get("resolution", 0.5)

        return f"""#!/usr/bin/env Rscript
# 单细胞分析（Seurat）
output_file <- {output_file}
marker_file <- {marker_file}

{self._single_cell_input_code(params)}
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
"""

    def _generate_spatial_code(self, params: dict[str, Any]) -> str:
        """Seurat Visium 空间流程（回退模板）。

        输入约定：spaceranger 输出目录（含 spatial/ 与 filtered_feature_bc_matrix/）。
        """
        input_file = _r_string(params.get("input_file", "spatial_dir"))
        output_file = _r_string(params.get("output_file", "spatial_clusters.csv"))
        plot_file = _r_string(params.get("plot_file", "spatial_plot.png"))
        resolution = params.get("resolution", 0.5)

        return f"""#!/usr/bin/env Rscript
# 空间转录组分析（Seurat Visium）
input_dir <- {input_file}
output_file <- {output_file}
plot_file <- {plot_file}

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
"""
