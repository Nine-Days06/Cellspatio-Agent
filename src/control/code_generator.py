import logging
from typing import Any

logger = logging.getLogger(__name__)

class CodeGenerator:
    """代码生成器，根据意图生成分析代码"""
    
    def __init__(self):
        self.analysis_templates = {
            'differential_expression': self._generate_de_template,
            'pathway_analysis': self._generate_pathway_template,
        }
    
    def generate_code(self, analysis_type: str, params: dict[str, Any]) -> str:
        """生成分析代码"""
        generator = self.analysis_templates.get(analysis_type)
        if generator:
            return generator(params)
        return f"# 不支持的分析类型: {analysis_type}"
    
    def _generate_de_template(self, params: dict[str, Any]) -> str:
        """生成差异表达分析模板"""
        input_file = params.get('input_file', 'data.csv')
        output_file = params.get('output_file', 'results.csv')
        
        return f"""
#!/usr/bin/env Rscript

# 差异表达分析
library(DESeq2)

# 读取数据
countData <- read.csv("{input_file}", row.names = 1)

# 创建 DESeq2 数据集
dds <- DESeqDataSetFromMatrix(
  countData = countData,
  colData = colData,
  design = ~ condition
)

# 运行差异分析
dds <- DESeq(dds)
res <- results(dds)

# 保存结果
write.csv(as.data.frame(res), "{output_file}")
"""
    
    def _generate_pathway_template(self, params: dict[str, Any]) -> str:
        """生成通路分析模板"""
        return """
#!/usr/bin/env Rscript

# 通路富集分析
library(clusterProfiler)
library(org.Hs.eg.db)

# 这里需要输入差异基因列表
# genes <- read.csv("deg_results.csv")$gene

# 富集分析
# ego <- enrichGO(gene = genes, OrgDb = org.Hs.eg.db, ont = "BP", pAdjustMethod = "BH")
"""