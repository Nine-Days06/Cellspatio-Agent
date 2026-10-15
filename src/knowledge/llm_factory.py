"""LightRAG 的 LLM 与 embedding 函数工厂"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# LightRAG 每次 LLM 调用结束都会 close client，不拦截则每次调用重建连接池。
# 传一个 aclose 为 no-op 的共享 client，让底层连接池跨调用复用。
_shared_http_client: httpx.AsyncClient | None = None


class _KeepAliveClient(httpx.AsyncClient):
    """aclose 置空：阻止 LightRAG 的 per-call close 销毁共享连接池"""

    async def aclose(self) -> None:
        """置空以阻止 LightRAG 的 per-call close 销毁共享连接池"""


def _shared_http() -> httpx.AsyncClient:
    """惰性创建并复用进程级共享 HTTP 连接池（跨 LLM 调用复用 TCP+TLS）"""
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = _KeepAliveClient(
            limits=httpx.Limits(max_connections=256, max_keepalive_connections=128),
            timeout=httpx.Timeout(240.0, connect=10.0),
        )
    return _shared_http_client


def build_llm_func(provider: str | None = None) -> tuple:
    """按 src.config 供应商配置构建 async LLM 函数与默认模型名

    返回的 llm_func 签名兼容 LightRAG 1.5.7：
        llm_func(prompt, system_prompt=None, history_messages=None, **kwargs)
    内部转发到 zhipu_complete_if_cache / openai_complete_if_cache。
    返回值：(llm_func, model_name)
    """
    from src.config import get_llm_config

    config = get_llm_config(provider)
    api_key = config["api_key"]
    model = config["model"]
    base_url = config.get("base_url")

    if config["client_type"] == "zhipuai":
        from lightrag.llm.zhipu import zhipu_complete_if_cache

        async def llm_func(prompt: str, system_prompt: str | None = None,
                           history_messages: list[dict] | None = None, **kwargs) -> str:
            return await zhipu_complete_if_cache(
                prompt=prompt, model=model, api_key=api_key,
                system_prompt=system_prompt, history_messages=history_messages or [],
                **kwargs,
            )
    else:
        from lightrag.llm.openai import openai_complete_if_cache

        async def llm_func(prompt: str, system_prompt: str | None = None,
                           history_messages: list[dict] | None = None, **kwargs) -> str:
            # openai_client_configs 注入共享连接池，跨调用复用 TCP+TLS
            return await openai_complete_if_cache(
                model=model, prompt=prompt, api_key=api_key,
                system_prompt=system_prompt, history_messages=history_messages,
                base_url=base_url,
                openai_client_configs={"http_client": _shared_http()},
                **kwargs,
            )

    return llm_func, model


def build_embedding_func(embedding_func=None):
    """返回 LightRAG 可用的 embedding 函数

    模型名取自 src.config.EMBEDDING_MODEL（env EMBEDDING_MODEL，未设时
    默认 bge-m3），即 Ollama 的 bge-m3:latest；模型名生效于 Ollama 调用
    （embed_model）与 LightRAG 的 model_name 属性（向量库隔离/建库锁定）。
    调用方可通过 OLLAMA_HOST 或 config['ollama_url'] 指向本地 Ollama。
    传入自定义 embedding_func 时原样返回。
    """
    if embedding_func is not None:
        return embedding_func
    from dataclasses import replace
    from functools import partial

    from lightrag.llm.ollama import ollama_embed

    from src.config import EMBEDDING_MODEL

    # Ollama 裸名等价 :latest 标签：补 tag 后与历史硬编码默认值
    # bge-m3:latest 完全一致（env 未设或设为 bge-m3 时行为零变化）
    model = EMBEDDING_MODEL if ":" in EMBEDDING_MODEL else f"{EMBEDDING_MODEL}:latest"
    # .func 取未包装的原始函数，用 partial 绑定 embed_model 使模型名真正生效
    inner = partial(ollama_embed.func, embed_model=model)

    async def _guarded(texts, **kwargs):
        """按需唤起 Ollama 后委托原始实现。

        透明性保证：签名与返回值形状不变、不抛新异常（ensure 失败也继续委托），
        因此对 LightRAG 与 KGMemory 两个调用方零感知。
        """
        from src.knowledge import ollama_runtime

        try:
            # ensure_ready 最坏持 _start_lock 30s+（启动轮询，pull 全程更久），
            # 必须 to_thread 卸载到工作线程——同步直调会冻结整个事件循环
            # （连 /api/sidebar 的请求一起卡死）
            await asyncio.to_thread(ollama_runtime.ensure_ready)
            ollama_runtime.touch()  # 毫秒级时间戳写入，保持同步即可
        except Exception as exc:  # noqa: BLE001 - 保活失败不应影响 embedding
            logger.warning("ollama ensure failed: %s", exc)
        return await inner(texts, **kwargs)

    return replace(ollama_embed, func=_guarded, model_name=model)