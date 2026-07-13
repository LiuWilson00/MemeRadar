"""設定管理測試（驗收：docs/TASKS.md P0-1）。"""

from pathlib import Path

import pytest

from memeradar.shared.config import ConfigError, Settings, get_settings


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert get_settings().anthropic_api_key == "sk-test-123"


def test_nvidia_keys_parsed_from_comma_list(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEYS", "nvapi-aaa, nvapi-bbb ,nvapi-ccc")
    keys = get_settings().nvidia_keys()
    assert keys == ["nvapi-aaa", "nvapi-bbb", "nvapi-ccc"]  # 去空白、忽略空項


def test_nvidia_keys_empty_when_unset(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEYS", raising=False)
    assert Settings(_env_file=None).nvidia_keys() == []


def test_defaults_allow_offline_dev(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    s = Settings(_env_file=None)  # 不讀 .env，驗證純預設值
    assert s.anthropic_api_key == ""
    assert s.memeradar_data_dir == Path("./data")


def test_require_raises_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        s.require("anthropic_api_key")


def test_require_returns_value(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "va-test")
    s = Settings(_env_file=None)
    assert s.require("voyage_api_key") == "va-test"
