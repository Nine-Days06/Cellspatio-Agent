"""PubMed ETL 配置"""
import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── 项目路径 ──────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
RAW_XML_DIR = DATA_DIR / "raw_xml"
PROC_DIR    = DATA_DIR / "processed"
OUTPUT_DIR  = DATA_DIR / "output"
LOG_DIR     = BASE_DIR / "logs"
PDF_DIR     = DATA_DIR / "pdfs"
DB_PATH     = PROC_DIR / "multiomics_lit.db"

# ── 网络代理 ──────────────────────────────────────────────────
PROXY = os.environ.get("PROXY", "") or None

# ── NCBI API 配置 ──────────────────────────────────────────────
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL   = os.environ.get("NCBI_EMAIL", "")
PMC_OA_API   = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

# API 请求间隔（秒）：有 Key 用 0.11，无 Key 用 0.34
REQUEST_INTERVAL = 0.11 if NCBI_API_KEY else 0.34

# 每批 efetch 的 PMID 数量（建议 200–500）
EFETCH_BATCH_SIZE = 300

# ── 搜索策略 ──────────────────────────────────────────────────
# 主搜索词：人类多组学 ×（转录组 / 代谢组 / 蛋白质组 / 基因组 / 表观组）
PUBMED_QUERY = (
    '("multi-omics"[Title/Abstract] OR "multi omics"[Title/Abstract] '
    'OR transcriptom*[Title/Abstract] OR metabolom*[Title/Abstract] '
    'OR proteom*[Title/Abstract] OR genom*[Title/Abstract] '
    'OR epigenom*[Title/Abstract] OR methylom*[Title/Abstract]) '
    'AND ('
    # 人类/疾病相关
    'human[Title/Abstract] OR patient[Title/Abstract] '
    'OR disease[Title/Abstract] OR cancer[Title/Abstract] '
    'OR tumor[Title/Abstract] OR "precision medicine"[Title/Abstract] '
    'OR "personalized medicine"[Title/Abstract] '
    'OR clinical[Title/Abstract] OR biomarker[Title/Abstract] '
    # 组学技术
    'OR "RNA-seq"[Title/Abstract] OR "ChIP-seq"[Title/Abstract] '
    'OR "ATAC-seq"[Title/Abstract] OR "mass spectrometry"[Title/Abstract] '
    'OR "gene expression"[Title/Abstract] OR "differential expression"[Title/Abstract] '
    # 生物学问题
    'OR mechanism[Title/Abstract] OR pathway[Title/Abstract] '
    'OR metabolism[Title/Abstract] OR phenotype[Title/Abstract] '
    'OR "genome-wide association"[Title/Abstract] OR GWAS[Title/Abstract])'
)

# 文献时间范围
SEARCH_YEAR_MIN = 2000
SEARCH_YEAR_MAX = datetime.now().year

# 每次搜索覆盖的年数，防止单次搜索结果超过 10,000 条分页限制
SEARCH_SLICE_YEARS = 5

# 发表年份硬过滤范围
PUB_YEAR_MIN = SEARCH_YEAR_MIN
PUB_YEAR_MAX = SEARCH_YEAR_MAX

# ── 硬过滤规则 ────────────────────────────────────────────────
ABSTRACT_MIN_LEN = 80

EXCLUDED_ARTICLE_TYPES = [
    "Letter", "Comment", "Correction", "Retraction",
    "Published Erratum", "Editorial", "News"
]

# ── LLM 配置 ──────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")

DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

ZHIPU_API_KEY   = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL     = os.environ.get("ZHIPU_MODEL", "glm-4-Flash-250414")
ZHIPU_BATCH_MODEL = "glm-4-flash"      # Batch API 使用的模型（价格 50% off）

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4")

LLM_BATCH_SIZE  = 5
LLM_CONCURRENCY = 2
LLM_MAX_TOKENS  = 8192
LLM_MAX_RETRIES = 3
LLM_MAX_ROUNDS  = 2

# ── LLM Batch API 配置（仅 zhipu） ──
LLM_BATCH_POLL_INTERVAL  = 30           # Batch 轮询间隔（秒）
LLM_BATCH_TIMEOUT        = 86400        # Batch 超时时间（24h）
LLM_BATCH_AUTO_DELETE    = True         # 完成后自动删除输入文件

# Provider 配置字典
LLM_PROVIDER_CONFIGS = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": DEEPSEEK_API_KEY,
        "client_type": "openai",
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "extra_kwargs": {
            "temperature": 0, "max_tokens": LLM_MAX_TOKENS,
            "timeout": 120, "response_format": {"type": "json_object"},
        },
        "extra_body": {"thinking": {"type": "disabled"}},
        "fix_multi_array": False,
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key": OPENAI_API_KEY,
        "client_type": "openai",
        "model": OPENAI_MODEL,
        "base_url": OPENAI_BASE_URL,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS, "timeout": 120},
        "fix_multi_array": False,
    },
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key": ZHIPU_API_KEY,
        "client_type": "zhipuai",
        "model": ZHIPU_MODEL,
        "base_url": None,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS},
        "fix_multi_array": True,
    },
}
