import os
from unittest.mock import patch

import pytest


@patch.dict(os.environ, {}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_default_llm_provider_is_deepseek():
    """无任何 env 时默认 deepseek"""
    # 重新导入以触发读取
    import importlib

    import src.config
    importlib.reload(src.config)
    assert src.config.LLM_PROVIDER == "deepseek"


@patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_legacy_llm_provider_compat():
    """仅设置 LLM_PROVIDER 向后兼容"""
    import importlib

    import src.config
    importlib.reload(src.config)
    assert src.config.LLM_PROVIDER == "openai"


@patch.dict(os.environ, {"AGENT_LLM_PROVIDER": "zhipu", "LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_agent_llm_provider_priority_over_legacy():
    """AGENT_LLM_PROVIDER 优先于兼容别名"""
    import importlib

    import src.config
    importlib.reload(src.config)
    assert src.config.LLM_PROVIDER == "zhipu"


@patch.dict(os.environ, {"AGENT_LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_agent_llm_provider_alone():
    """仅设置 AGENT_LLM_PROVIDER 生效"""
    import importlib

    import src.config
    importlib.reload(src.config)
    assert src.config.LLM_PROVIDER == "openai"


def test_get_llm_config_valid():
    """get_llm_config 返回正确配置"""
    import src.config
    cfg = src.config.get_llm_config("zhipu")
    assert cfg["client_type"] == "zhipuai"
    assert cfg["model"] == "glm-4-Flash-250414"


def test_get_llm_config_invalid_raises():
    """无效 provider 抛 ValueError"""
    import src.config
    with pytest.raises(ValueError, match="不支持的 LLM 供应商"):
        src.config.get_llm_config("unknown")