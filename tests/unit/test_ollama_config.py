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
