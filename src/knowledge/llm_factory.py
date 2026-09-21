"""LightRAG 的 LLM 与 embedding 函数工厂"""
import logging

logger = logging.getLogger(__name__)


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

        base_url = base_url or "https://open.bigmodel.cn/api/paas/v4"

        async def llm_func(prompt: str, system_prompt: str | None = None,
                           history_messages: list[dict] | None = None, **kwargs) -> str:
            # zhipu_complete_if_cache(prompt, model=..., api_key=..., system_prompt=..., history_messages=[])
            # base_url 用于创建 client，不传给 completion
            return await zhipu_complete_if_cache(
                prompt=prompt, model=model, api_key=api_key,
                system_prompt=system_prompt, history_messages=history_messages or [],
                **kwargs,
            )
    else:
        from lightrag.llm.openai import openai_complete_if_cache

        async def llm_func(prompt: str, system_prompt: str | None = None,
                           history_messages: list[dict] | None = None, **kwargs) -> str:
            # openai_complete_if_cache(model, prompt, system_prompt=..., history_messages=..., base_url=..., api_key=...)
            return await openai_complete_if_cache(
                model=model, prompt=prompt, api_key=api_key,
                system_prompt=system_prompt, history_messages=history_messages,
                base_url=base_url, **kwargs,
            )

    return llm_func, model


def build_embedding_func(embedding_func=None):
    """返回 LightRAG 可用的 embedding 函数

    默认使用 Ollama bge-m3（lightrag.llm.ollama.ollama_embed 内置的
    EmbeddingFunc，1024 维、model_name='bge-m3:latest'）。
    调用方可通过 OLLAMA_HOST 或 config['ollama_url'] 指向本地 Ollama。
    传入自定义 embedding_func 时原样返回。
    """
    if embedding_func is not None:
        return embedding_func
    from lightrag.llm.ollama import ollama_embed

    return ollama_embed