# -*- coding: utf-8 -*-
"""wm.config: env overrides, readiness flags, data-dir creation."""
from pathlib import Path

import pytest

import wm.config as config_module
from wm.config import Config


def test_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("WM_AI_API_KEY", "sk-test-123")
    monkeypatch.setenv("WM_AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("WM_AI_MODEL", "test-model")
    monkeypatch.setenv("WM_AI_TEMPERATURE", "0.42")
    monkeypatch.setenv("WM_HEADLESS", "true")
    monkeypatch.setenv("WM_SLOW_MO", "7")
    monkeypatch.setenv("WM_MONITOR_PORT", "9321")
    monkeypatch.setenv("WM_MONITOR_ENABLED", "0")
    monkeypatch.setenv("WM_MAX_SCORE", "20")
    monkeypatch.setenv("WM_MAX_RETRIES", "5")

    cfg = Config()

    assert cfg.AI_API_KEY == "sk-test-123"
    assert cfg.AI_BASE_URL == "https://example.test/v1"
    assert cfg.AI_MODEL == "test-model"
    assert cfg.AI_TEMPERATURE == pytest.approx(0.42)
    assert cfg.HEADLESS is True
    assert cfg.SLOW_MO == 7
    assert cfg.MONITOR_PORT == 9321
    assert cfg.MONITOR_ENABLED is False
    assert cfg.MAX_SCORE == pytest.approx(20)
    assert cfg.MAX_RETRIES == 5


def test_legacy_ai_env_names_are_accepted(monkeypatch):
    monkeypatch.delenv("WM_AI_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "legacy-key")
    monkeypatch.setenv("AI_BASE_URL", "https://legacy.test/v1")
    monkeypatch.setenv("AI_MODEL", "legacy-model")

    cfg = Config()

    assert cfg.AI_API_KEY == "legacy-key"
    assert cfg.AI_BASE_URL == "https://legacy.test/v1"
    assert cfg.AI_MODEL == "legacy-model"


def test_new_ai_env_names_win_over_legacy(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "legacy-key")
    monkeypatch.setenv("WM_AI_API_KEY", "new-key")

    cfg = Config()

    assert cfg.AI_API_KEY == "new-key"


def test_invalid_numeric_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("WM_SLOW_MO", "not-a-number")
    monkeypatch.setenv("WM_MAX_SCORE", "abc")

    cfg = Config()

    assert cfg.SLOW_MO == 100
    assert cfg.MAX_SCORE == pytest.approx(13.0)


def test_ai_ready_requires_api_key(monkeypatch):
    monkeypatch.setenv("WM_AI_API_KEY", "")
    assert Config().ai_ready is False

    monkeypatch.setenv("WM_AI_API_KEY", "sk-x")
    assert Config().ai_ready is True


def test_wechat_ready_requires_enabled_and_webhook(monkeypatch):
    monkeypatch.setenv("WM_WECHAT_ENABLED", "true")
    monkeypatch.setenv("WM_WECHAT_WEBHOOK_URL", "")
    assert Config().wechat_ready is False

    monkeypatch.setenv("WM_WECHAT_WEBHOOK_URL", "https://hook.test/x")
    assert Config().wechat_ready is True

    monkeypatch.setenv("WM_WECHAT_ENABLED", "false")
    assert Config().wechat_ready is False


def test_default_data_dirs_are_created(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    cfg = Config()

    for d in (cfg.DATA_DIR, cfg.RUBRIC_DIR, cfg.HISTORY_DIR,
              cfg.SCREENSHOTS_DIR, cfg.RUNTIME_DIR, cfg.MONITOR_DIR):
        assert d.is_dir(), f"directory not created: {d}"

    # all under the (patched) project root
    for d in (cfg.DATA_DIR, cfg.RUNTIME_DIR):
        assert Path(d).resolve().is_relative_to(tmp_path.resolve())


def test_wm_data_and_runtime_dir_overrides(monkeypatch, tmp_path):
    data_dir = tmp_path / "wmdata"
    runtime_dir = tmp_path / "wmruntime"
    monkeypatch.setenv("WM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("WM_RUNTIME_DIR", str(runtime_dir))

    cfg = Config()

    assert cfg.DATA_DIR == data_dir
    assert cfg.HISTORY_DIR == data_dir / "history"
    assert cfg.RUBRIC_DIR == data_dir / "rubrics"
    assert cfg.SCREENSHOTS_DIR == data_dir / "screenshots"
    assert cfg.RUNTIME_DIR == runtime_dir
    assert cfg.MONITOR_DIR == runtime_dir / "monitor"
    for d in (cfg.DATA_DIR, cfg.HISTORY_DIR, cfg.RUBRIC_DIR,
              cfg.RUNTIME_DIR, cfg.MONITOR_DIR):
        assert d.is_dir(), f"directory not created: {d}"


def test_app_metadata():
    cfg = Config()
    assert cfg.APP_NAME == "WhisperMark 2.0"
    assert cfg.APP_VERSION == "2.0.0"
