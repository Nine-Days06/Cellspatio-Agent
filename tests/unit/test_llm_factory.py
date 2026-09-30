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
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    seen: dict = {}

    async def fake_inner(texts, **kwargs):
        seen["embed_model"] = kwargs.get("embed_model")
        return [[0.0]]

    monkeypatch.setattr("src.config.EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(ollama_runtime, "ensure_ready", lambda: None)
    monkeypatch.setattr(ollama_runtime, "touch", lambda: None)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    import asyncio
    embedding = build_embedding_func()
    assert getattr(embedding, "model_name", "") == "nomic-embed-text:latest"
    asyncio.run(embedding.func(["test"]))
    assert seen["embed_model"] == "nomic-embed-text:latest"


def test_build_embedding_func_passthrough_custom():
    custom = object()
    assert build_embedding_func(embedding_func=custom) is custom


# ── T4：embedding 包裹层用例 ──────────────────────────────────


def test_build_embedding_func_returns_async_wrapper():
    """包裹层必须是 async（lightrag 的 ollama_embed.func 实测为 async）。"""
    import inspect

    func = build_embedding_func().func
    assert inspect.iscoroutinefunction(func)


def test_wrapper_calls_ensure_ready_and_touch(monkeypatch):
    """每次 embedding 调用前先 ensure_ready + touch。"""
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    calls: list[str] = []
    monkeypatch.setattr(ollama_runtime, "ensure_ready", lambda: calls.append("ensure"))
    monkeypatch.setattr(ollama_runtime, "touch", lambda: calls.append("touch"))

    async def fake_inner(texts, **kwargs):
        return [[0.0]]

    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    import asyncio
    asyncio.run(build_embedding_func().func(["hello"], max_token_size=8192))

    assert calls == ["ensure", "touch"]


def test_wrapper_delegates_to_ollama_embed(monkeypatch):
    """包裹层必须把 texts 原样委托给原始实现并返回其结果。"""
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    seen: dict = {}

    async def fake_inner(texts, **kwargs):
        seen["texts"] = texts
        seen["kwargs"] = kwargs
        return [[0.1, 0.2]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", lambda: None)
    monkeypatch.setattr(ollama_runtime, "touch", lambda: None)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    import asyncio
    result = asyncio.run(build_embedding_func().func(["hello"]))

    assert seen["texts"] == ["hello"]
    assert seen["kwargs"]["embed_model"] == "bge-m3:latest"
    assert result == [[0.1, 0.2]]


def test_wrapper_preserves_model_name(monkeypatch):
    """model_name 不变 → LightRAG 的向量库隔离/建库锁定语义零变化。"""
    from src import config

    monkeypatch.setattr(config, "EMBEDDING_MODEL", "bge-m3")
    assert build_embedding_func().model_name == "bge-m3:latest"


def test_custom_embedding_func_is_not_wrapped():
    """传入自定义 embedding_func 时原样返回，不被 Ollama 包裹。"""

    def custom(texts, **kwargs):
        return [[0.0]]

    assert build_embedding_func(custom) is custom


def test_wrapper_failure_of_ensure_does_not_break_call(monkeypatch):
    """ensure_ready 抛异常时仍继续委托原始实现（不把异常带给 LightRAG）。"""
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    def boom():
        raise RuntimeError("state file corrupted")

    async def fake_inner(texts, **kwargs):
        return [[1.0]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", boom)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    import asyncio
    assert asyncio.run(build_embedding_func().func(["x"])) == [[1.0]]


def test_wrapper_keeps_event_loop_alive_while_ensure_blocks(monkeypatch):
    """ensure_ready 阻塞时事件循环不被卡死（必须 to_thread，不能同步直调）。

    实测依据：ensure_ready 最坏持 _start_lock 30s+；若同步直调，
    事件循环线程被占满，scenario 内的 asyncio.sleep 永远排不上，
    总耗时 ≥ release 超时（5s）→ 断言 <2s 失败（红）。
    """
    import asyncio
    import threading as th
    import time as time_mod

    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    release = th.Event()

    def blocking_ensure():
        release.wait(timeout=5)  # 模拟最坏 30s 的启动/拉模型持锁

    async def fake_inner(texts, **kwargs):
        return [[0.0]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", blocking_ensure)
    monkeypatch.setattr(ollama_runtime, "touch", lambda: None)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    async def scenario():
        task = asyncio.ensure_future(build_embedding_func().func(["x"]))
        await asyncio.sleep(0.05)  # 同步直调时这行根本排不上（loop 线程被占）
        release.set()              # 走到这里才放行阻塞的 ensure
        return await task

    start = time_mod.monotonic()
    assert asyncio.run(scenario()) == [[0.0]]
    assert time_mod.monotonic() - start < 2  # to_thread：<0.1s；同步直调：≥5s