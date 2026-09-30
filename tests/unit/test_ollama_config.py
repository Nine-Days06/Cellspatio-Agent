"""配置读取用例：不受 test_ollama_runtime 的 clean_runtime 夹具替换影响。"""
from __future__ import annotations

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
