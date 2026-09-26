import pytest

from src.knowledge.llm_factory import build_embedding_func, build_llm_func


@pytest.mark.asyncio
async def test_build_llm_func_calls_openai(monkeypatch):
    calls = {}

    async def fake_cache(model, prompt, system_prompt=None, history_messages=None, **kwargs):
        calls["model"] = model
        calls["prompt"] = prompt
        calls["system_prompt"] = system_prompt
        calls["history_messages"] = history_messages
        return "ok"

    monkeypatch.setattr("lightrag.llm.openai.openai_complete_if_cache", fake_cache)

    llm_func, _ = build_llm_func("deepseek")
    result = await llm_func("hi")
    assert result == "ok"
    assert calls["model"] == "deepseek-flash"
    assert calls["prompt"] == "hi"


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

    async def fake_cache(prompt, model, api_key, system_prompt=None, history_messages=None, **kwargs):
        calls["base_url"] = kwargs.get("base_url", "https://open.bigmodel.cn/api/paas/v4")
        return "ok"

    monkeypatch.setattr("lightrag.llm.zhipu.zhipu_complete_if_cache", fake_cache)

    llm_func, model = build_llm_func("zhipu")
    assert model == "glm-4"
    await llm_func("test prompt")
    assert calls["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_build_embedding_func_default_is_ollama_bge(monkeypatch):
    monkeypatch.setattr("src.config.EMBEDDING_MODEL", "bge-m3")  # 模拟 env 未设时的 config 默认值
    embedding = build_embedding_func()
    assert getattr(embedding, "model_name", "") == "bge-m3:latest"  # 默认值回退，行为零变化
    assert getattr(embedding, "embedding_dim", 0) == 1024


def test_build_embedding_func_propagates_config_model(monkeypatch):
    """EMBEDDING_MODEL 配置值真正传播到 embedding 调用（embed_model）"""
    monkeypatch.setattr("src.config.EMBEDDING_MODEL", "nomic-embed-text")
    embedding = build_embedding_func()
    assert getattr(embedding, "model_name", "") == "nomic-embed-text:latest"
    assert embedding.func.keywords["embed_model"] == "nomic-embed-text:latest"


def test_build_embedding_func_passthrough_custom():
    custom = object()
    assert build_embedding_func(embedding_func=custom) is custom