import json
import logging
import re
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class IntentParser:
    """意图解析器，使用 LLM 进行智能意图识别"""

    # 意图定义
    INTENT_TYPES: ClassVar[dict[str, Any]] = {
        "analysis": {
            "description": "数据分析请求：差异表达分析、通路富集、可视化等",
            "sub_types": {
                "differential_expression": "差异表达分析、差异基因、DEG、fold change",
                "pathway_analysis": "通路分析、通路富集、GO、KEGG、Pathway",
                "visualization": "可视化、画图、图表、火山图、热图、PCA、箱线图",
                "single_cell": "单细胞分析、scRNA、Seurat、降维、聚类、UMAP、细胞类型注释、细胞亚群",
                "spatial": "空间转录组、空间组学、Visium、spatial、时空、空间位置、组织原位",
            }
        },
        "fetch_data": {
            "description": "数据获取请求：下载公共数据库数据",
            "sub_types": {
                "geo": "GEO 数据集、表达谱、GSE、GSM、series matrix",
                "kegg": "KEGG 通路、代谢通路、KO、pathway map",
                "uniprot": "UniProt 蛋白、蛋白序列、蛋白结构、UniProt ID",
            }
        },
        "knowledge_query": {
            "description": "知识查询请求：基因/蛋白/通路/疾病/药物等生物学知识问答",
            "sub_types": {}
        },
        "general": {
            "description": "闲聊、问候、感谢、无关话题、无法识别的输入",
            "sub_types": {}
        }
    }

    def __init__(self, llm_client=None, model: str = "gpt-4o-mini"):
        self.llm_client = llm_client
        self.model = model

        # 关键词匹配回退用
        self.analysis_keywords = {
            'differential_expression': ['差异表达', '差异基因', 'DEG', 'fold change'],
            'pathway_analysis': ['通路', 'pathway', '富集', 'GO', 'KEGG'],
            'visualization': ['可视化', '画图', '图表', '火山图', '热图'],
            'single_cell': ['单细胞', 'scRNA', 'scrna', 'Seurat', 'seurat', 'UMAP', '细胞聚类', '细胞注释'],
            'spatial': ['空间转录', '空间组', 'Visium', 'visium', 'spatial', '时空组'],
        }
        self.knowledge_keywords = [
            '是什么', '有什么', '有哪些', '哪些', '怎么', '如何',
            '作用', '功能', '关系', '解释', '机制', '途径', '通路',
            '含义', '意义', '定义', '介绍', '简介', '概述',
            '特点', '特征', '特性', '分类', '类型', '种类'
        ]
        self.dataset_keywords = ['下载', '获取数据', '数据集', '找数据', '数据下载']
        self.source_keywords = {
            'geo': ['geo', 'gse', '数据集', '表达谱'],
            'kegg': ['kegg', 'pathway', '通路'],
            'uniprot': ['uniprot', '蛋白', 'protein'],
        }

        self._build_system_prompt()

    def _build_system_prompt(self) -> None:
        """构建系统提示词"""
        intent_desc = []
        for intent_type, info in self.INTENT_TYPES.items():
            sub_desc = ""
            if info["sub_types"]:
                sub_items = [f"  - {k}: {v}" for k, v in info["sub_types"].items()]
                sub_desc = "\n" + "\n".join(sub_items)
            intent_desc.append(f"- {intent_type}: {info['description']}{sub_desc}")

        self.system_prompt = f"""你是 CellSpatio 单细胞与时空组学分析智能体的意图识别器。请分析用户输入，准确识别意图类型。

## 意图类型定义：
{chr(10).join(intent_desc)}

## 处理规则：
1. **单一明确意图**：直接返回对应类型
2. **混合意图**（如"下载数据并分析"）：
   - 优先识别主要动作（通常第一个动词）
   - 标记 secondary_intent 字段
3. **模糊/歧义输入**（如"TP53"、"基因"）：
   - 返回 type="ambiguous"
   - 在 clarification 字段给出澄清建议
4. **上下文相关**：如果用户之前有操作，结合上下文判断

## 输出格式（严格 JSON）：
{{
  "type": "analysis|fetch_data|knowledge_query|general|ambiguous",
  "analysis_type": "differential_expression|pathway_analysis|visualization|single_cell|spatial",  // 仅 type=analysis
  "secondary_intent": "fetch_data|analysis|knowledge_query|null",  // 次要意图
  "confidence": 0.0-1.0,
  "original_input": "用户原始输入",
  "clarification": "当 type=ambiguous 时的澄清问题，否则 null"
}}

## 示例：
输入："帮我下载 GSE123456 并分析差异表达"
输出：{{"type":"fetch_data","secondary_intent":"analysis","confidence":0.9,"original_input":"帮我下载 GSE123456 并分析差异表达","clarification":null}}

输入："TP53"
输出：{{"type":"ambiguous","confidence":0.6,"original_input":"TP53","clarification":"请问您想查询 TP53 的基因信息、下载 TP53 蛋白数据、还是进行 TP53 相关分析？"}}

输入："画个火山图"
输出：{{"type":"analysis","analysis_type":"visualization","confidence":0.95,"original_input":"画个火山图","clarification":null}}
"""

    def parse(self, user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """解析用户输入，返回意图"""
        if self.llm_client:
            try:
                return self._parse_with_llm(user_input, context)
            except (json.JSONDecodeError, AttributeError, ValueError) as e:
                logger.warning(f"LLM intent parsing failed, fallback to keywords: {e}")

        return self._parse_with_keywords(user_input)

    def _parse_with_llm(self, user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """使用 LLM 进行意图识别"""
        messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        if context and context.get("history"):
            # 添加对话历史作为上下文
            for msg in context["history"][-3:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": f"用户输入：{user_input}\n请输出 JSON 格式意图："})

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            result["original_input"] = user_input

            # 验证必需字段
            required = ["type", "confidence", "original_input"]
            for field in required:
                if field not in result:
                    result[field] = user_input if field == "original_input" else (0.5 if field == "confidence" else "general")

            # 处理 ambiguous 类型
            if result.get("type") == "ambiguous" and not result.get("clarification"):
                result["clarification"] = "您的输入比较简短，能否详细说明想要做什么？"

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON: {e}")
            raise
        except Exception as e:
            logger.warning(f"LLM parsing failed: {e}")
            raise

    def _parse_with_keywords(self, user_input: str) -> dict[str, Any]:
        """关键词匹配回退方案（无 LLM 或 LLM 失败时）"""
        # 检查是否为分析意图
        for analysis_type, keywords in self.analysis_keywords.items():
            for keyword in keywords:
                if keyword in user_input:
                    return {
                        'type': 'analysis',
                        'analysis_type': analysis_type,
                        'confidence': 0.8,
                        'original_input': user_input,
                        'secondary_intent': None,
                        'clarification': None,
                    }

        # 检查是否为数据获取意图
        for keyword in self.dataset_keywords:
            if keyword in user_input:
                return {
                    'type': 'fetch_data',
                    'confidence': 0.75,
                    'original_input': user_input,
                    'secondary_intent': None,
                    'clarification': None,
                }

        # 检查是否为知识查询
        for keyword in self.knowledge_keywords:
            if keyword in user_input:
                return {
                    'type': 'knowledge_query',
                    'confidence': 0.7,
                    'original_input': user_input,
                    'secondary_intent': None,
                    'clarification': None,
                }

        # 简短输入可能是 ambiguous
        all_analysis_keywords = [kw for kws in self.analysis_keywords.values() for kw in kws]
        if len(user_input.strip()) < 10 and not any(k in user_input for k in self.knowledge_keywords + self.dataset_keywords + all_analysis_keywords):
            return {
                'type': 'ambiguous',
                'confidence': 0.5,
                'original_input': user_input,
                'secondary_intent': None,
                'clarification': '您的输入比较简短，能否详细说明想要做什么？例如：查询基因信息、下载数据集、分析数据等。',
            }

        return {
            'type': 'general',
            'confidence': 0.5,
            'original_input': user_input,
            'secondary_intent': None,
            'clarification': None,
        }

    def extract_parameters(self, user_input: str) -> dict[str, Any]:
        """从用户输入中提取参数"""
        params = {}

        file_pattern = r'[\w/\\:\-\.]+\.(?:csv|tsv|fastq|vcf|fasta)'
        files = re.findall(file_pattern, user_input)
        if files:
            params['input_files'] = files

        gene_pattern = r'\b[A-Z][A-Z0-9]{1,10}\b'
        genes = re.findall(gene_pattern, user_input)
        if genes:
            params['genes'] = genes

        sources = []
        for source, keywords in self.source_keywords.items():
            if any(k in user_input for k in keywords):
                sources.append(source)
        if sources:
            params['sources'] = sources

        gse_pattern = r'\bGSE\d+\b'
        gses = re.findall(gse_pattern, user_input.upper())
        if gses:
            params['dataset_ids'] = gses

        return params