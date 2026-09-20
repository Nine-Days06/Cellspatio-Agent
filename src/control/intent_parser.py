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
        
        return params