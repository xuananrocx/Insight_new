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
    assert value.web_search_enabled is False


def test_web_search_switch_survives_legacy_update(config_env):
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(
        max_tokens=81920, timeout_seconds=600, web_search_enabled=True))
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(
        max_tokens=81920, timeout_seconds=600))
    assert routes_settings.get_ai_generation().web_search_enabled is True
    assert yaml.safe_load(config_env.read_text())['qa']['ai_generation']['web_search_enabled'] is True


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


def test_summary_settings_persist_and_keep_legacy_fields(config_env):
    from src.api.routes_settings import AISummaryConfig, update_ai_summary_config, get_ai_summary_config
    result = update_ai_summary_config(AISummaryConfig(enabled=True, output_tokens=20000, output_token_limit=40000, read_timeout_seconds=180))
    assert result['effective_read_timeout_seconds'] == 180
    update_ai_summary_config(AISummaryConfig(enabled=False, min_word_count=150))
    saved = yaml.safe_load(config_env.read_text())['ingest']['ai_summary']
    assert saved['output_tokens'] == 20000 and saved['read_timeout_seconds'] == 180
    settings._config = yaml.safe_load(config_env.read_text())
    assert get_ai_summary_config()['output_token_limit'] == 40000


def test_summary_config_write_failure_does_not_change_memory(config_env, monkeypatch):
    before = routes_settings.get_ai_summary_config()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(routes_settings, '_secure_write_yaml', fail)
    with pytest.raises(OSError):
        routes_settings.update_ai_summary_config(routes_settings.AISummaryConfig(output_tokens=8000))
    assert routes_settings.get_ai_summary_config() == before


def test_tool_timeout_and_large_context_persist_without_legacy_reset(config_env):
    result = routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(
        max_tokens=4096, timeout_seconds=600, context_chars=900000, tool_timeout_seconds=90))
    assert result.context_chars == 900000 and result.tool_timeout_seconds == 90
    result = routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=8192, timeout_seconds=600))
    assert result.context_chars == 900000 and result.tool_timeout_seconds == 90
    for invalid in (0, 601):
        with pytest.raises(HTTPException):
            routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(
                max_tokens=8192, timeout_seconds=600, tool_timeout_seconds=invalid))


def test_job_limits_persist_and_old_client_preserves_them(config_env):
    from pydantic import ValidationError
    from src.qa.answer_jobs import limits
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(
        max_tokens=4096, timeout_seconds=600, job_timeout_seconds=7200, job_max_concurrent=2))
    assert limits() == (7200, 2)
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=4096, timeout_seconds=300))
    assert limits() == (7200, 2)
    for kwargs in ({'job_timeout_seconds': 29}, {'job_timeout_seconds': 86401}, {'job_max_concurrent': 0}, {'job_max_concurrent': 65}):
        with pytest.raises(ValidationError):
            routes_settings.AiGenerationConfig(max_tokens=4096, timeout_seconds=600, **kwargs)


def test_independent_web_switch_persists_without_changing_other_fields(config_env):
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=4096, timeout_seconds=300))
    routes_settings.update_web_search_config(routes_settings.WebSearchConfig(enabled=True))
    saved = yaml.safe_load(config_env.read_text())
    assert saved['app']['name'] == 'Insight'
    assert saved['qa']['ai_generation'] == {'max_tokens': 4096, 'timeout_seconds': 300, 'web_search_enabled': True}
    settings._config = saved
    assert routes_settings.get_web_search_config().enabled is True
    routes_settings.update_ai_generation(routes_settings.AiGenerationConfig(max_tokens=8192, timeout_seconds=600))
    assert routes_settings.get_web_search_config().enabled is True
    routes_settings.update_web_search_config(routes_settings.WebSearchConfig(enabled=False))
    assert yaml.safe_load(config_env.read_text())['qa']['ai_generation']['web_search_enabled'] is False


def test_independent_web_switch_write_failure_keeps_saved_state(config_env, monkeypatch):
    routes_settings.update_web_search_config(routes_settings.WebSearchConfig(enabled=True))
    before = config_env.read_text()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(routes_settings, '_secure_write_yaml', fail)
    with pytest.raises(OSError):
        routes_settings.update_web_search_config(routes_settings.WebSearchConfig(enabled=False))
    assert routes_settings.get_web_search_config().enabled is True
    assert config_env.read_text() == before
