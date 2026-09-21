import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── 项目路径 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent

# ── 网络代理配置 ────────────────────────────────────────────
PROXY = os.environ.get("PROXY", "") or None

# ── LLM 供应商配置 ──────────────────────────────────────────
# 快捷切换：修改 AGENT_LLM_PROVIDER 即可切换供应商
# 向后兼容：若未设置 AGENT_LLM_PROVIDER，仍读取 LLM_PROVIDER
# 支持：deepseek / openai / zhipu
LLM_PROVIDER = (
    os.environ.get("AGENT_LLM_PROVIDER")
    or os.environ.get("LLM_PROVIDER")
    or "deepseek"
)
LLM_MAX_TOKENS  = int(os.environ.get("LLM_MAX_TOKENS", "8192"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "3"))
LLM_TIMEOUT     = int(os.environ.get("LLM_TIMEOUT", "120"))

# DeepSeek
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 智谱AI
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_MODEL   = os.environ.get("ZHIPU_MODEL", "glm-4-Flash-250414")

# OpenAI 兼容 API
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4")

# ── Provider 配置字典 ────────────────────────────────────────
LLM_PROVIDER_CONFIGS = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key": DEEPSEEK_API_KEY,
        "client_type": "openai",
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "extra_kwargs": {
            "temperature": 0, "max_tokens": LLM_MAX_TOKENS,
            "timeout": LLM_TIMEOUT, "response_format": {"type": "json_object"},
        },
        "extra_body": {"thinking": {"type": "disabled"}},
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key": OPENAI_API_KEY,
        "client_type": "openai",
        "model": OPENAI_MODEL,
        "base_url": OPENAI_BASE_URL,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS, "timeout": LLM_TIMEOUT},
    },
    "zhipu": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key": ZHIPU_API_KEY,
        "client_type": "zhipuai",
        "model": ZHIPU_MODEL,
        "base_url": None,
        "extra_kwargs": {"temperature": 0, "max_tokens": LLM_MAX_TOKENS},
    },
}


def get_llm_config(provider: str | None = None) -> dict[str, Any]:
    """获取当前 LLM 配置"""
    provider = provider or LLM_PROVIDER
    if provider not in LLM_PROVIDER_CONFIGS:
        raise ValueError(f"不支持的 LLM 供应商: {provider}，可选: {list(LLM_PROVIDER_CONFIGS.keys())}")
    return LLM_PROVIDER_CONFIGS[provider]


def get_current_llm():
    """获取当前配置的 LLM 客户端"""
    import zhipuai
    from openai import OpenAI

    config = get_llm_config()

    if config["client_type"] == "zhipuai":
        client = zhipuai.ZhipuAI(api_key=config["api_key"])
        return client, config["model"]
    else:
        client = OpenAI(
            api_key=config["api_key"],
            base_url=config["base_url"],
        )
        return client, config["model"]


# ── 外部数据源配置 ──────────────────────────────────────────
# GEO 检索复用 NCBI E-utilities
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL   = os.environ.get("NCBI_EMAIL", "")

# KEGG 免费 API key（可选，提升频率限额）
KEGG_API_KEY = os.environ.get("KEGG_API_KEY", "")

# 本地 Ollama（embedding 用，计划 2 消费）
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
