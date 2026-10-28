"""Validated, task-scoped summary policy. No credentials are persisted here."""
from __future__ import annotations

from contextvars import ContextVar
from pydantic import BaseModel, Field, model_validator
from src.core.config import settings


class SummaryPolicy(BaseModel):
    output_tokens: int = Field(16000, ge=256, le=262144)
    output_token_limit: int = Field(32000, ge=256, le=262144)
    read_timeout_seconds: int | None = Field(None, ge=10, le=3600)
    step_timeout_seconds: int = Field(600, ge=30, le=7200)
    task_timeout_seconds: int = Field(3600, ge=60, le=86400)
    request_limit: int = Field(80, ge=1, le=1000)
    retry_count: int = Field(1, ge=0, le=3)
    segment_chars: int = Field(30000, ge=2000, le=200000)
    reduce_chars: int = Field(16000, ge=4000, le=100000)
    auto_full_calls: int = Field(12, ge=1, le=100)
    split_depth: int = Field(1, ge=0, le=3)
    checkpoint_retention_days: int = Field(90, ge=1, le=3650)

    @model_validator(mode='after')
    def consistent(self):
        if self.output_token_limit < self.output_tokens:
            raise ValueError('补偿输出上限不能低于初始输出额度')
        if self.task_timeout_seconds < self.step_timeout_seconds:
            raise ValueError('整个任务时长不能小于单步骤时长')
        return self


active_policy: ContextVar[SummaryPolicy | None] = ContextVar('summary_policy', default=None)
request_check = ContextVar('summary_request_check', default=lambda: None)
reduce_checkpoint = ContextVar('summary_reduce_checkpoint', default=None)


def policy() -> SummaryPolicy:
    frozen = active_policy.get()
    if frozen is not None:
        return frozen
    cfg = settings.config.get('ingest', {}).get('ai_summary', {}) or {}
    return SummaryPolicy.model_validate({k: v for k, v in cfg.items() if k in SummaryPolicy.model_fields})


def effective_policy() -> SummaryPolicy:
    from src.core.api_retry import configured_read_timeout
    current = policy()
    if active_policy.get() is not None:
        return current
    from src.core import accounts
    model = accounts.provider_snapshot.get() or {}
    values = current.model_dump()
    values['read_timeout_seconds'] = current.read_timeout_seconds or int(configured_read_timeout())
    if model.get('output_tokens'):
        values['output_tokens'] = min(values['output_tokens'], model['output_tokens'])
        values['output_token_limit'] = min(values['output_token_limit'], model['output_tokens'])
    if model.get('context_tokens'):
        available = model['context_tokens'] - values['output_token_limit'] - 1024
        if available < 4000:
            raise ValueError('摘要输出预留与模型上下文容量不匹配，请降低摘要输出额度或修正模型能力')
        # Conservative character budget, not a claim of exact provider tokenization.
        values['segment_chars'] = min(values['segment_chars'], available)
        values['reduce_chars'] = min(values['reduce_chars'], available)
    return SummaryPolicy.model_validate(values)


def generation_signature() -> dict:
    p = effective_policy()
    return dict(version='summary-stream-v1', output_tokens=p.output_tokens,
                output_token_limit=p.output_token_limit, segment_chars=p.segment_chars,
                reduce_chars=p.reduce_chars)


def config_sources() -> dict[str, str]:
    """Only expose source names, never arbitrary configuration values."""
    import yaml
    from src.core.config import USER_CONFIG_PATH, USER_CONFIG_LOCAL_PATH, PROJECT_ROOT
    sources = {key: '默认值' for key in [*SummaryPolicy.model_fields, 'enabled', 'min_word_count']}
    for path, label in [(USER_CONFIG_PATH, '用户配置'), (USER_CONFIG_LOCAL_PATH, '用户本地覆盖'),
                        (PROJECT_ROOT / 'config.local.yaml', '项目本地覆盖')]:
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            values = raw.get('ingest', {}).get('ai_summary', {}) or {}
            for key in values:
                if key in sources:
                    sources[key] = label
    return sources
