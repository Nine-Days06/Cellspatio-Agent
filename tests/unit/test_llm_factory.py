import pytest

from src.knowledge.llm_factory import build_embedding_func, build_llm_func


@pytest.mark.asyncio
async def test_build_llm_func_calls_openai(monkeypatch):
    calls = {}

    async def fake_cache(api_key, model, messages, base_url=None, **kwargs):
        calls["api_key"] = api_key
        calls["model"] = model
        calls["base_url"] = base_url
        return "ok"

    monkeypatch.setattr("lightrag.llm.openai.openai_complete_if_cache", fake_cache)

    llm_func, _ = build_llm_func("deepseek")
    result = await llm_func("deepseek-v4-flash", [{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert calls["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_build_llm_func_zhipu_uses_openai_compat(monkeypatch):
    monkeypatch.setattr("src.config.LLM_PROVIDER_CONFIGS", {
        "zhipu": {
            "api_key": "", "api_key_env": "ZHIPU_API_KEY",
            "client_type": "zhipuai", "model": "glm-4", "base_url": None,
            "extra_kwargs": {},
        },
    })
    calls = {}

    async def fake_cache(api_key, model, messages, base_url=None, **kwargs):
        calls["base_url"] = base_url
        return "ok"

    monkeypatch.setattr("lightrag.llm.zhipu.zhipu_complete_if_cache", fake_cache)

    llm_func, model = build_llm_func("zhipu")
    assert model == "glm-4"
    await llm_func("glm-4", [])
    assert calls["base_url"].startswith("https://open.bigmodel.cn")


def test_build_embedding_func_default_is_ollama_bge():
    embedding = build_embedding_func()
    assert getattr(embedding, "model_name", "") == "bge-m3:latest"
    assert getattr(embedding, "embedding_dim", 0) == 1024


def test_build_embedding_func_passthrough_custom():
    custom = object()
    assert build_embedding_func(embedding_func=custom) is custom