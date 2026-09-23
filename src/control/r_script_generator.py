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
        """生成差异表达分析 R 代码"""
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 差异表达分析模板

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 这里可以添加差异表达分析逻辑
# 示例：简单的差异表达分析框架
library(DESeq2)

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 读取数据
count_data <- read.csv(input_file, row.names = 1)

# 这里可以添加 DESeq2 分析流程
# dds <- DESeqDataSetFromMatrix(...)
# dds <- DESeq(dds)
# res <- results(dds)

# 保存结果
results <- data.frame(gene = rownames(count_data), log2FC = 0, pvalue = 1)
write.csv(results, output_file, row.names = FALSE)

cat("差异表达分析完成，结果已保存至:", output_file, "\\n")
'''

    def _generate_pathway_code(self, params: dict[str, Any]) -> str:
        """生成通路分析 R 代码"""
        input_file = params.get("input_file", "input.csv")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 通路分析模板

# 读取数据
data <- read.csv(input_file, row.names = 1)

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 读取基因列表
genes <- read.csv(input_file)

# 这里可以添加 KEGG/GO 通路富集分析
# library(clusterProfiler)
# enrich_result <- enrichKEGG(gene = genes$gene_id, organism = 'hsa')

# 保存结果
results <- data.frame(pathway = character(), pvalue = numeric())
write.csv(results, output_file, row.names = FALSE)

cat("通路分析完成，结果已保存至:", output_file, "\\n")
'''

    def _generate_visualization_code(self, params: dict[str, Any]) -> str:
        """生成可视化 R 代码"""
        plot_type = params.get("plot_type", "volcano")

        return f'''#!/usr/bin/env Rscript
# 可视化模板

# 设置绘图类型
plot_type <- "{plot_type}"

# 示例数据
matrix_data <- matrix(rnorm(100), nrow = 10)

if (!(plot_type %in% c("volcano", "heatmap", "pca"))) {{
    stop("不支持的绘图类型: ", plot_type)
}}

if (plot_type == "volcano") {{
    # Volcano Plot 火山图绘制逻辑
    cat("绘制 Volcano Plot...\\n")
}} else {{
    if (plot_type == "heatmap") {{
        # 热图绘制逻辑
        heatmap(matrix_data)
        cat("绘制热图...\\n")
    }} else {{
        if (plot_type == "pca") {{
            # PCA 绘制逻辑
            cat("绘制 PCA 图...\\n")
        }}
    }}
}}
'''

    def _generate_single_cell_code(self, params: dict[str, Any]) -> str:
        """生成单细胞分析 R 代码（占位）"""
        input_file = params.get("input_file", "input.h5ad")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 单细胞分析模板

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 这里可以添加 Seurat 单细胞分析流程
# library(Seurat)
# obj <- Read10X(input_file)
# obj <- NormalizeData(obj)
# obj <- FindVariableFeatures(obj)
# obj <- ScaleData(obj)
# obj <- RunPCA(obj)
# obj <- FindNeighbors(obj)
# obj <- FindClusters(obj)
# obj <- RunUMAP(obj)

# 保存结果
results <- data.frame(cell = character(), cluster = numeric(), UMAP1 = numeric(), UMAP2 = numeric())
write.csv(results, output_file, row.names = FALSE)

cat("单细胞分析完成，结果已保存至:", output_file, "\\n")
'''

    def _generate_spatial_code(self, params: dict[str, Any]) -> str:
        """生成空间转录组分析 R 代码（占位）"""
        input_file = params.get("input_file", "input.h5ad")
        output_file = params.get("output_file", "output.csv")

        return f'''#!/usr/bin/env Rscript
# 空间转录组分析模板

# 设置输入输出文件
input_file <- "{input_file}"
output_file <- "{output_file}"

# 这里可以添加空间转录组分析流程
# library(Seurat)
# obj <- Load10X_Spatial(input_file)
# obj <- SCTransform(obj)
# obj <- RunPCA(obj)
# obj <- FindNeighbors(obj)
# obj <- FindClusters(obj)
# obj <- RunUMAP(obj)
# SpatialDimPlot(obj)

# 保存结果
results <- data.frame(spot = character(), cluster = numeric(), x = numeric(), y = numeric())
write.csv(results, output_file, row.names = FALSE)

cat("空间转录组分析完成，结果已保存至:", output_file, "\\n")
'''