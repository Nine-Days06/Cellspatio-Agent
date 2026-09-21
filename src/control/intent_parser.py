import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class IntentParser:
    """意图解析器，理解用户自然语言输入"""
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client
        self.analysis_keywords = {
            'differential_expression': ['差异表达', '差异基因', 'DEG', 'fold change'],
            'pathway_analysis': ['通路', 'pathway', '富集', 'GO', 'KEGG'],
            'visualization': ['可视化', '画图', '图表', '火山图', '热图'],
        }
        self.knowledge_keywords = ['是什么', '作用', '功能', '关系', '解释']
        
        # fetch_data 相关关键词
        self.dataset_keywords = ['下载', '获取数据', '数据集', '找数据', '数据下载']
        self.source_keywords = {
            'geo': ['geo', 'gse', '数据集', '表达谱'],
            'kegg': ['kegg', 'pathway', '通路'],
            'uniprot': ['uniprot', '蛋白', 'protein'],
        }
    
    def parse(self, user_input: str) -> dict[str, Any]:
        """解析用户输入，返回意图"""
        # 简化实现，实际应使用 LLM 进行更准确的解析
        
        # 检查是否为分析意图
        for analysis_type, keywords in self.analysis_keywords.items():
            for keyword in keywords:
                if keyword in user_input:
                    return {
                        'type': 'analysis',
                        'analysis_type': analysis_type,
                        'confidence': 0.8,
                        'original_input': user_input
                    }
        
        # 检查是否为数据获取意图
        for keyword in self.dataset_keywords:
            if keyword in user_input:
                return {
                    'type': 'fetch_data',
                    'confidence': 0.75,
                    'original_input': user_input
                }
        
        # 检查是否为知识查询
        for keyword in self.knowledge_keywords:
            if keyword in user_input:
                return {
                    'type': 'knowledge_query',
                    'confidence': 0.7,
                    'original_input': user_input
                }
        
        # 默认为通用对话
        return {
            'type': 'general',
            'confidence': 0.5,
            'original_input': user_input
        }
    
    def extract_parameters(self, user_input: str) -> dict[str, Any]:
        """从用户输入中提取参数"""
        params = {}
        
        # 提取文件路径
        file_pattern = r'[\w/\\:\-\.]+\.(?:csv|tsv|fastq|vcf|fasta)'
        files = re.findall(file_pattern, user_input)
        if files:
            params['input_files'] = files
        
        # 提取基因名称
        gene_pattern = r'\b[A-Z][A-Z0-9]{1,10}\b'
        genes = re.findall(gene_pattern, user_input)
        if genes:
            params['genes'] = genes
        
        # 提取数据来源
        sources = []
        for source, keywords in self.source_keywords.items():
            if any(k in user_input for k in keywords):
                sources.append(source)
        if sources:
            params['sources'] = sources
        
        # 提取数据集编号（GSE）
        gse_pattern = r'\bGSE\d+\b'
        gses = re.findall(gse_pattern, user_input.upper())
        if gses:
            params['dataset_ids'] = gses
        
        return params