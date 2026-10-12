"""统一 AI 生成配置卡：设置 API、回退优先级与 qa_chat 重试预算。"""
import time

import pytest
import yaml
from fastapi import HTTPException

from src.api import routes_settings
from src.core.config import settings


@pytest.fixture
def config_env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, '_config', {'qa': {}})
    user_config = tmp_path / 'config.yaml'
    user_config.write_text(yaml.safe_dump({'app': {'name': 'Insight'}}), encoding='utf-8')
    monkeypatch.setattr('src.core.config.USER_CONFIG_PATH', user_config)
    return user_config


def test_get_returns_defaults_without_config(config_env):
    value = routes_settings.get_ai_generation()
    assert value.max_tokens == 81920 and value.timeout_seconds == 600 and value.context_chars == 48000


def test_get_prefers_ai_generation_over_legacy_deep_ai(config_env):
    settings._config['qa']['deep_ai'] = {'max_tokens': 4096, 'context_chars': 20000}
    value = routes_settings.get_ai_generation()
    assert value.max_tokens == 4096 and value.context_chars == 20000
    settings._config['qa']['ai_generation'] = {'max_tokens': 99999, 'timeout_seconds': 300}
    value = routes_settings.get_ai_generation()
    assert value.max_tokens == 99999 and value.timeout_seconds == 300 and value.context_chars == 20000


def test_put_persists_and_rejects_out_of_range(config_env):
    result = routes_settings.update_ai_generation(
        routes_settings.AiGenerationConfig(max_tokens=131072, timeout_seconds=900, context_chars=60000))
    assert result.max_tokens == 131072 and result.timeout_seconds == 900
    saved = yaml.safe_load(config_env.read_text(encoding='utf-8'))
    assert saved['qa']['ai_generation'] == {'max_tokens': 131072, 'timeout_seconds': 900, 'context_chars': 60000}
    assert settings.config['qa']['ai_generation']['timeout_seconds'] == 900
    with pytest.raises(HTTPException) as info:
        routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=100, timeout_seconds=900))
    assert info.value.status_code == 400
    with pytest.raises(HTTPException):
        routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=4096, timeout_seconds=5))
    with pytest.raises(HTTPException):
        routes_settings.update_ai_generation(
            routes_settings.AiGenerationConfig(max_tokens=4096, timeout_seconds=900, context_chars=100))


def test_qa_chat_budget_reads_ai_generation_timeout(config_env):
    from src.core import api_retry
    settings._config['qa']['ai_generation'] = {'timeout_seconds': 1200}
    budget = api_retry.RetryBudget.for_scene('qa_chat')
    assert budget.maximum == 10 and budget.deadline > time.monotonic() + 1100
    settings._config['qa']['ai_generation']['timeout_seconds'] = 5
    assert api_retry.RetryBudget.for_scene('qa_chat').deadline <= time.monotonic() + 30
