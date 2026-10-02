import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ── 项目路径 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent

# ── LLM 供应商配置 ──────────────────────────────────────────
# 快捷切换：修改 AGENT_LLM_PROVIDER 即可切换供应商
# 向后兼容：若未设置 AGENT_LLM_PROVIDER，仍读取 LLM_PROVIDER
# 支持：deepseek / openai / zhipu
LLM_PROVIDER: str = (
    os.environ.get("AGENT_LLM_PROVIDER")
    or os.environ.get("LLM_PROVIDER")
    or "zhipu"
)
LLM_MAX_TOKENS  = int(os.environ.get("LLM_MAX_TOKENS", "8192"))
LLM_TIMEOUT     = int(os.environ.get("LLM_TIMEOUT", "120"))

# DeepSeek
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

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

# 本地 Ollama（embedding 用）
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
# 自动唤起：embedding 首次被使用时若 Ollama 未运行则拉起 ollama serve
OLLAMA_AUTOSTART = os.environ.get("OLLAMA_AUTOSTART", "1").strip().lower() not in (
    "0", "false", "no", "off", "",
)
# 自动拉取：缺模型时是否自动下载（默认 0=不自动下载）。下载是用户资产决策：
# 模型目录由服务端 OLLAMA_MODELS 决定，自动拉取可能灌到错误目录。
OLLAMA_AUTO_PULL = os.environ.get("OLLAMA_AUTO_PULL", "0").strip().lower() in ("1", "true", "yes", "on")
# 随程序启动：应用启动时后台预热 Ollama，省掉首次提问前的等待。
# 实测（隔离冷实例）：「拉起服务→可应答」占首轮等待约 70%（4.22s / 5.99s），
# 模型从磁盘载入内存只占约 30%（1.77s），热态 0.03s——所以启动时拉起服务就能
# 吃掉大头，无需预热 embedding（那要长驻约 1.4GB 内存，不划算）。
# 开启时同时不做空闲自动关闭（见 ollama_runtime._idle_seconds），否则空闲后照样
# 被关掉，预热白做。默认 0 = 保持既有的按需唤起行为。
OLLAMA_EAGER_START = os.environ.get("OLLAMA_EAGER_START", "0").strip().lower() in ("1", "true", "yes", "on")
# 自动关闭：空闲超过该分钟数后关闭「本应用启动的」Ollama（<=0 表示不自动关闭）
try:
    OLLAMA_IDLE_MINUTES = int(os.environ.get("OLLAMA_IDLE_MINUTES", "10"))
except ValueError:
    # 手误的环境变量不该让整个应用起不来：回退默认并留痕
    OLLAMA_IDLE_MINUTES = 10
