import os
from unittest.mock import patch

import pytest


@pytest.fixture
def reload_config():
    """返回一个重新加载 config 模块的函数"""
    import importlib

    import src.config

    def _reload():
        importlib.reload(src.config)
        return src.config

    return _reload


@patch.dict(os.environ, {}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_default_llm_provider_is_zhipu(reload_config):
    """无任何 env 时默认 zhipu"""
    config = reload_config()
    assert config.LLM_PROVIDER == "zhipu"


@patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_legacy_llm_provider_compat(reload_config):
    """仅设置 LLM_PROVIDER 向后兼容"""
    config = reload_config()
    assert config.LLM_PROVIDER == "openai"


@patch.dict(os.environ, {"AGENT_LLM_PROVIDER": "zhipu", "LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_agent_llm_provider_priority_over_legacy(reload_config):
    """AGENT_LLM_PROVIDER 优先于兼容别名"""
    config = reload_config()
    assert config.LLM_PROVIDER == "zhipu"


@patch.dict(os.environ, {"AGENT_LLM_PROVIDER": "openai"}, clear=True)
@patch("dotenv.load_dotenv", lambda: None)
def test_agent_llm_provider_alone(reload_config):
    """仅设置 AGENT_LLM_PROVIDER 生效"""
    config = reload_config()
    assert config.LLM_PROVIDER == "openai"


@patch.dict(os.environ, {"AGENT_LLM_PROVIDER": "zhipu"}, clear=True)
def test_get_llm_config_valid(reload_config):
    """get_llm_config 返回正确配置"""
    config = reload_config()
    cfg = config.get_llm_config("zhipu")
    assert cfg["client_type"] == "zhipuai"
    assert cfg["model"] == "glm-4-Flash-250414"


def test_get_llm_config_invalid_raises():
    """无效 provider 抛 ValueError"""
    import src.config
    with pytest.raises(ValueError, match="不支持的 LLM 供应商"):
        src.config.get_llm_config("unknown")

