"""配置读取用例：不受 test_ollama_runtime 的 clean_runtime 夹具替换影响。"""
from __future__ import annotations

import importlib

from src import config
from src.knowledge import ollama_runtime as rt


def test_autostart_reads_config_flag(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_AUTOSTART", False)
    assert rt._autostart() is False
    monkeypatch.setattr(config, "OLLAMA_AUTOSTART", True)
    assert rt._autostart() is True


def test_idle_seconds_reads_config_minutes(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 3)
    assert rt._idle_seconds() == 180.0
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 0)
    assert rt._idle_seconds() == 0.0


# ── C1：OLLAMA_IDLE_MINUTES 容错解析 ──────────────────────────


def test_idle_minutes_invalid_value_falls_back_to_default(monkeypatch):
    """非法值（如 'ten'）不应让 import src.config 失败，应回退默认 10。"""
    monkeypatch.setenv("OLLAMA_IDLE_MINUTES", "ten")
    importlib.reload(config)
    try:
        assert config.OLLAMA_IDLE_MINUTES == 10
    finally:
        monkeypatch.delenv("OLLAMA_IDLE_MINUTES", raising=False)
        importlib.reload(config)


def test_idle_minutes_empty_string_falls_back_to_default(monkeypatch):
    """空串 OLLAMA_IDLE_MINUTES= 不应让 import src.config 失败，应回退默认 10。"""
    monkeypatch.setenv("OLLAMA_IDLE_MINUTES", "")
    importlib.reload(config)
    try:
        assert config.OLLAMA_IDLE_MINUTES == 10
    finally:
        monkeypatch.delenv("OLLAMA_IDLE_MINUTES", raising=False)
        importlib.reload(config)


# ── I1：OLLAMA_AUTOSTART falsy 判定补全 ───────────────────────


import pytest


@pytest.mark.parametrize("val", ["FALSE", "No", "OFF", " off "])
def test_autostart_falsy_variants_are_false(val, monkeypatch):
    """大写/混合/带空格的 falsy 值应被识别为关闭。"""
    monkeypatch.setenv("OLLAMA_AUTOSTART", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_AUTOSTART is False
    finally:
        monkeypatch.delenv("OLLAMA_AUTOSTART", raising=False)
        importlib.reload(config)


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", " 1 "])
def test_autostart_truthy_variants_are_true(val, monkeypatch):
    """各种 truthy 值应被识别为开启。"""
    monkeypatch.setenv("OLLAMA_AUTOSTART", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_AUTOSTART is True
    finally:
        monkeypatch.delenv("OLLAMA_AUTOSTART", raising=False)
        importlib.reload(config)


# ── T1 追加：OLLAMA_AUTO_PULL 配置读取 ───────────────────────────────


def test_auto_pull_reads_config_flag(monkeypatch):
    """OLLAMA_AUTO_PULL 读取配置：falsy 值返回 False，truthy 值返回 True。"""
    monkeypatch.setattr(config, "OLLAMA_AUTO_PULL", False)
    assert rt._auto_pull() is False
    monkeypatch.setattr(config, "OLLAMA_AUTO_PULL", True)
    assert rt._auto_pull() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_auto_pull_falsy_variants_are_false(val, monkeypatch):
    """各种 falsy 值应被识别为关闭（默认行为）。"""
    monkeypatch.setenv("OLLAMA_AUTO_PULL", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_AUTO_PULL is False
    finally:
        monkeypatch.delenv("OLLAMA_AUTO_PULL", raising=False)
        importlib.reload(config)


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", " 1 "])
def test_auto_pull_truthy_variants_are_true(val, monkeypatch):
    """各种 truthy 值应被识别为开启。"""
    monkeypatch.setenv("OLLAMA_AUTO_PULL", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_AUTO_PULL is True
    finally:
        monkeypatch.delenv("OLLAMA_AUTO_PULL", raising=False)
        importlib.reload(config)


# ── 随程序启动（OLLAMA_EAGER_START）────────────────────────────
# 实测（隔离冷实例，端口 8611）：ollama serve 启动→/api/tags 可应答 4.22s（占首轮
# 等待 70%），首次 embedding 的模型加载仅 1.77s（30%），热态 0.03s。
# 因此「随程序启动拉起服务」即能吃掉大头；而常驻又要求不做空闲自动关闭，
# 否则空闲 10 分钟后被关掉，下一次提问照样付 4.22s。


def test_eager_start_reads_config_flag(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", True)
    assert rt._eager_start() is True
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", False)
    assert rt._eager_start() is False


def test_eager_start_suppresses_idle_autoclose(monkeypatch):
    """开启随程序启动 → 空闲阈值归零（0 = 不自动关闭），否则预热白做。"""
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", True)
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 10)
    assert rt._idle_seconds() == 0.0


def test_idle_autoclose_unchanged_when_eager_off(monkeypatch):
    """未开启时行为不变：仍按 OLLAMA_IDLE_MINUTES 空闲关闭。"""
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", False)
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 10)
    assert rt._idle_seconds() == 600.0


def test_start_mode_reflects_config(monkeypatch):
    """侧栏只读展示用：模式字符串必须随配置变化。"""
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", True)
    assert rt.start_mode() == "eager"
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", False)
    assert rt.start_mode() == "lazy"


def test_eager_start_defaults_off(monkeypatch):
    """默认 0（关闭）：不设环境变量时不得改变既有行为。"""
    monkeypatch.delenv("OLLAMA_EAGER_START", raising=False)
    importlib.reload(config)
    try:
        assert config.OLLAMA_EAGER_START is False
    finally:
        importlib.reload(config)


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", " 1 "])
def test_eager_start_truthy_variants_are_true(val, monkeypatch):
    monkeypatch.setenv("OLLAMA_EAGER_START", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_EAGER_START is True
    finally:
        monkeypatch.delenv("OLLAMA_EAGER_START", raising=False)
        importlib.reload(config)


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_eager_start_falsy_variants_are_false(val, monkeypatch):
    monkeypatch.setenv("OLLAMA_EAGER_START", val)
    importlib.reload(config)
    try:
        assert config.OLLAMA_EAGER_START is False
    finally:
        monkeypatch.delenv("OLLAMA_EAGER_START", raising=False)
        importlib.reload(config)
