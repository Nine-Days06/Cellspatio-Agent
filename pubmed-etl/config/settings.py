# config/settings.py
"""
全局配置文件
使用前请填入你的 NCBI API Key（免费申请：https://www.ncbi.nlm.nih.gov/account/）
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 项目路径 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_XML_DIR = DATA_DIR / "raw_xml"
PROC_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = BASE_DIR / "logs"

# PDF 存储路径
PDF_DIR = DATA_DIR / "pdfs"

DB_PATH = (
    PROC_DIR / "multiomics_lit.db"
)  # 文件名保留 multiomics_lit.db 以兼容存量数据；展示名已改为 CellSpatio

# ── 网络代理配置 ────────────────────────────────────────────
# 可选：HTTP/HTTPS 代理，如 http://127.0.0.1:7890（走代理访问 NCBI 等外网）
# 留空则直连
PROXY = os.environ.get("PROXY", "") or None

# ── NCBI API 配置 ────────────────────────────────────────────
# 填入你的 API Key，可将速率从 3 次/秒 提升到 10 次/秒
# 留空也可运行，但会更慢
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "")  # NCBI 要求提供联系邮箱，建议设置到 .env

# PMC OA 资源桶（PMC Cloud Service on AWS S3，公共匿名读，无需签名）
# 注：NCBI 已于 2026-08 永久退役 PMC OA Web Service（oa.fcgi 返回 404），
# 原 PMC_OA_API 端点不可用，OA 链接改为通过该 S3 桶列举版本与读取元数据
PMC_S3_URL = "https://pmc-oa-opendata.s3.amazonaws.com"

# API 请求间隔（秒）：有 Key 用 0.11，无 Key 用 0.34
REQUEST_INTERVAL = 0.11 if NCBI_API_KEY else 0.34

# 每批 efetch 的 PMID 数量（建议 200–500）
EFETCH_BATCH_SIZE = 300

# ── 搜索策略 ─────────────────────────────────────────────────
# 主搜索词：聚焦人类单细胞与空间/时序组学
# 自由词（含常见同义变体与平台名）+ MeSH 权威词，策略偏召回，
# 精度由后端硬过滤与 LLM 二次验证兜底
PUBMED_QUERY = (
    # 单细胞：含拼写变体（连字符/无连字符）与染色质方向
    '("single-cell"[Title/Abstract] OR "single cell"[Title/Abstract] OR '
    "scRNA-seq[Title/Abstract] OR scRNAseq[Title/Abstract] OR "
    'snRNA-seq[Title/Abstract] OR "single-cell RNA sequencing"[Title/Abstract] OR '
    '"single nucleus"[Title/Abstract] OR '
    '"single-cell sequencing"[Title/Abstract] OR '
    "CITE-seq[Title/Abstract] OR "
    "scATAC-seq[Title/Abstract] OR snATAC-seq[Title/Abstract] OR "
    "RNA velocity[Title/Abstract] OR "
    '"single-cell atlas"[Title/Abstract] OR "cell atlas"[Title/Abstract] OR '
    # 空间/时序：覆盖主流平台（成像式 + 测序式）与常见短语变体；裸 spatiotemporal 会引入
    # 大量非组学论文，限定为 transcriptomics/omics 组合
    '"spatial transcriptomics"[Title/Abstract] OR spatialomics[Title/Abstract] OR '
    '"spatial omics"[Title/Abstract] OR "spatial multi-omics"[Title/Abstract] OR '
    # 裸 "spatially resolved" 会引入大量光学/环境/材料噪声（实测 2565 命中里 1350 条无任何
    # 组学词，53%），必须带组学宾语才保留：spatially resolved transcriptomics/proteomics/…
    '"spatially resolved transcriptomics"[Title/Abstract] OR '
    '"spatially resolved proteomics"[Title/Abstract] OR '
    '"spatially resolved metabolomics"[Title/Abstract] OR '
    '"spatially resolved genomics"[Title/Abstract] OR '
    '"spatially resolved multi-omics"[Title/Abstract] OR '
    '"spatially resolved omics"[Title/Abstract] OR '
    '"spatially resolved mass spectrometry"[Title/Abstract] OR '
    '"spatially resolved imaging"[Title/Abstract] OR '
    "Visium[Title/Abstract] OR MERFISH[Title/Abstract] OR "
    '"Slide-seq"[Title/Abstract] OR Slide-seqV2[Title/Abstract] OR '
    '"Stereo-seq"[Title/Abstract] OR seqFISH[Title/Abstract] OR '
    "STARmap[Title/Abstract] OR Xenium[Title/Abstract] OR "
    "GeoMx[Title/Abstract] OR CosMx[Title/Abstract] OR "
    '"spatial ATAC-seq"[Title/Abstract] OR '
    '"spatiotemporal transcriptomics"[Title/Abstract] OR '
    '"spatiotemporal omics"[Title/Abstract] OR '
    # 空间/单细胞蛋白组（对应 LLM 收录规则 7：CyTOF、IMC、MIBI 等）
    "CyTOF[Title/Abstract] OR "
    '"imaging mass cytometry"[Title/Abstract] OR "mass cytometry"[Title/Abstract] OR '
    "MIBI[Title/Abstract] OR CODEX[Title/Abstract] OR "
    '"single-cell proteomics"[Title/Abstract] OR '
    '"spatial proteomics"[Title/Abstract] OR '
    '"spatial genomics"[Title/Abstract] OR "spatial epigenomics"[Title/Abstract] OR '
    '"spatial metabolomics"[Title/Abstract] OR '
    # noexp 禁止 explode：该词默认 explode 会异常扩散（实测 10 万+命中）
    '"Single-Cell Analysis"[MeSH Terms] OR "Spatial Transcriptomics"[MeSH Terms:noexp]) AND '
    # 物种过滤用 MeSH 规范词 Humans："Homo sapiens"[Organism] 不是有效字段标签，
    # PubMed 会回落为全字段文本匹配，物种过滤不生效（实测 query translation 证实）
    '("Humans"[MeSH Terms] OR human[Title/Abstract] OR patients[Title/Abstract])'
    # NOT 前置排除（检索端省下载，与后端硬过滤口径对齐）：
    # 只排撤稿 / 更正 / 勘误类。综述、letter、comment、editorial、news 不排除，
    # 由 LLM 验证按主题相关性收录（方法库与知识问答库都需要观点性/综述内容）。
    # 注：correction[pt]/retraction[pt] 是无效 PT 名，PubMed 会静默丢弃；
    # 撤稿类有效写法是 retracted publication[pt] 与 "expression of concern"[pt]，
    # 更正类由 "published erratum"[pt] 覆盖
    ' NOT ("published erratum"[pt] OR retracted publication[pt] OR "expression of concern"[pt])'
)

# 文献时间范围
SEARCH_YEAR_MIN = 1900
SEARCH_YEAR_MAX = datetime.now().year

# 每次搜索的初分段年数；单段命中超过 10,000 条硬限制时，
# downloader 会按日期自动对半细分（年→月→日），保证不截断
SEARCH_SLICE_YEARS = 5

# 发表年份硬过滤范围（与检索范围保持一致）
PUB_YEAR_MIN = SEARCH_YEAR_MIN
PUB_YEAR_MAX = SEARCH_YEAR_MAX

# ── 硬过滤规则 ────────────────────────────────────────────────
# 需要排除的文章类型（PubMed PublicationType 字段，子串匹配）。
# 只排内容不可信的类别：撤稿 / 更正 / 勘误 / 关注声明。
# Letter / Comment / Editorial / News 不在此列——由 LLM 验证按主题相关性判断。
# "Retraction" 是旧 PT 名（Retraction of Publication）；现行名 Retracted Publication
# 不含该子串，需单独列出；Expression of Concern 同理纳入
EXCLUDED_ARTICLE_TYPES = [
    "Correction",
    "Retraction",
    "Retracted Publication",
    "Expression of Concern",
    "Published Erratum",
]

# ── LLM 验证配置 ──────────────────────────────────────────────
# 快捷切换：修改 ETL_LLM_PROVIDER 即可切换供应商
# 向后兼容：若未设置 ETL_LLM_PROVIDER，仍读取 LLM_PROVIDER
# 支持：deepseek / openai / zhipu
# 注：空字符串视同未设置，将回退
LLM_PROVIDER = os.environ.get("ETL_LLM_PROVIDER") or os.environ.get("LLM_PROVIDER") or "zhipu"

# DeepSeek（OpenAI 兼容格式）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 智谱AI（原生 zhipuai SDK）
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL = "glm-4-Flash-250414"
ZHIPU_BATCH_MODEL = "glm-4-flash"  # Batch API 使用的模型（价格 50% off）

# 其他 OpenAI 兼容 API（如 OpenAI、SiliconFlow、vLLM 等）
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get(
    "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "qwen3.7-plus")

LLM_BATCH_SIZE = 5  # 每次调用验证的文献数量
LLM_CONCURRENCY = 2  # 并行发送的批次数（同时进行的 API 调用数）
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8192"))  # 每次 API 调用的最大 token 数
LLM_MAX_RETRIES = int(
    os.environ.get("LLM_MAX_RETRIES", "3")
)  # 单次 API 调用重试次数（指数退避 2s/4s/8s）
LLM_MAX_ROUNDS = 2  # 轮次重试次数（初始 1 轮 + 额外重试轮数）

# ── LLM Batch API 配置（仅 zhipu） ──
LLM_BATCH_POLL_INTERVAL = 30  # Batch 轮询间隔（秒）
LLM_BATCH_TIMEOUT = 86400  # Batch 超时时间（24h）
LLM_BATCH_AUTO_DELETE = True  # 完成后自动删除输入文件

# 智谱 Batch 官方限制：单个 batch 文件 ≤ 100MB、每文件 ≤ 50000 个请求、
# 每个 batch 文件只能包含对单个模型的请求、每个请求必须带唯一 custom_id。
# 本项目的 batch 输入为「每篇文献一行 JSONL」，提示词固定开销约 1.1KB，
# 行体积随摘要长度变化（实测均值约 7.6KB/行），故 100MB 字节上限通常
# 先于 5 万条上限触发（约 1.3 万条即触顶）。超出即自动拆分为多个批次。
#
# 上限取 95,000,000 字节（十进制 95MB）而非 95*1024*1024：服务端对
# "100MB" 的计量口径未明说，若按十进制 100_000_000 计算，则
# 95*1024*1024=99,614,720 只剩 0.39% 余量，遇到行体积估算偏差就会踩线。
# 95,000,000 对两种口径（十进制 100MB / 二进制 100MiB）都留有 5% 以上余量。
LLM_BATCH_MAX_BYTES = int(os.environ.get("LLM_BATCH_MAX_BYTES", "95000000"))
LLM_BATCH_MAX_REQUESTS = int(os.environ.get("LLM_BATCH_MAX_REQUESTS", "50000"))

# Provider 配置字典 — 新增 provider 只需在此添加一项
LLM_PROVIDER_CONFIGS = {
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_fallback": ZHIPU_API_KEY,
        "client_type": "zhipuai",
        "model": ZHIPU_MODEL,
        "base_url": None,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS},
        "fix_multi_array": True,
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_fallback": DEEPSEEK_API_KEY,
        "client_type": "openai",
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "extra_kwargs": {
            "temperature": 0,
            "max_tokens": LLM_MAX_TOKENS,
            "timeout": 120,
            "response_format": {"type": "json_object"},
        },
        # V4 模型思考模式默认开启：temperature 不生效、思考 token 占用输出预算
        # 可能导致 JSON 截断，显式关闭
        "extra_body": {"thinking": {"type": "disabled"}},
        "fix_multi_array": False,
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key_fallback": OPENAI_API_KEY,
        "client_type": "openai",
        "model": OPENAI_MODEL,
        "base_url": OPENAI_BASE_URL,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS, "timeout": 120},
        "fix_multi_array": False,
    },
}
