"""Shared model constraints and request-scoped budgets. No model calls or storage writes."""
from __future__ import annotations

import copy
import json
import logging
import math
from contextvars import ContextVar
from dataclasses import dataclass, asdict

from pydantic import BaseModel, Field, ConfigDict

APP_WINDOW = 262144
SAFETY = 3000
log = logging.getLogger(__name__)


class ContextSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_window_tokens: int = Field(65536, ge=16384, le=APP_WINDOW)
    history_tokens: int = Field(8000, ge=1000, le=24000)
    summary_input_tokens: int = Field(10000, ge=2000, le=20000)
    summary_tokens: int = Field(2400, ge=500, le=4000)
    answer_reserve_tokens: int = Field(16384, ge=1024, le=131072)


def context_values(config):
    legacy = (config.get('feature_flags') or {}).get('conversation_context') or {}
    correct = (config.get('qa') or {}).get('conversation_context') or {}
    values = {}
    for key, field in ContextSettings.model_fields.items():
        raw = correct.get(key, legacy.get(key, field.default))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = field.default
        schema = ContextSettings.model_json_schema()['properties'][key]
        values[key] = max(schema['minimum'], min(schema['maximum'], value))
    return values


def estimate(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return math.ceil(len(text.encode('utf-8')) / 3)


@dataclass(frozen=True)
class Decision:
    window: int
    window_source: str
    model_output: int | None
    requested_output: int
    input_estimate: int
    safety: int
    actual_output: int
    limited_by: str

    def notes(self):
        labels = {'scene': '场景输出封顶', 'model': '模型最大输出', 'context': '上下文剩余空间'}
        return (f'窗口 {self.window:,}（{self.window_source}） · 输入约 {self.input_estimate:,} · '
                f'实际输出上限 {self.actual_output:,} tokens · 限制来源：{labels[self.limited_by]}'
                + (' · 模型最大输出未配置' if self.model_output is None else ''))


class BudgetError(ValueError):
    pass


@dataclass(frozen=True)
class Snapshot:
    qa: dict
    context: dict
    model_context: int | None
    model_output: int | None

    @property
    def window(self):
        return min(self.model_context or self.context['context_window_tokens'], APP_WINDOW)

    @property
    def window_source(self):
        if self.model_context and self.model_context > APP_WINDOW:
            return '应用支持上限'
        return '模型配置' if self.model_context else '系统默认窗口'

    def cap(self, requested):
        return min(int(requested), self.model_output) if self.model_output else int(requested)

    def reserve(self, requested):
        # A planning target, not a provider output cap. Leave room in small windows.
        return min(self.cap(requested), self.context['answer_reserve_tokens'], self.window // 2)

    def decide(self, requested, input_estimate, safety=SAFETY):
        remaining = self.window - input_estimate - safety
        if remaining < 1:
            raise BudgetError(f'上下文空间不足：窗口 {self.window}，输入估算 {input_estimate}，安全预留 {safety}。请减少资料或核对模型容量。')
        actual = min(self.cap(requested), remaining)
        if actual < 1:
            raise BudgetError('模型输出预算必须大于零')
        cause = 'context' if remaining < self.cap(requested) else ('model' if self.model_output and self.model_output < requested else 'scene')
        return Decision(self.window, self.window_source, self.model_output, requested, input_estimate, safety, actual, cause)


active: ContextVar[Snapshot | None] = ContextVar('model_budget', default=None)


def capture(provider=None):
    from src.core.config import settings
    from src.core import accounts
    config = settings.config
    model = provider if provider is not None else (accounts.provider_snapshot.get() or {})
    def positive(key):
        v = model.get(key)
        return v if type(v) is int and v > 0 else None
    return Snapshot(copy.deepcopy(config.get('qa') or {}), context_values(config), positive('context_tokens'), positive('output_tokens'))


def current():
    return active.get() or capture()


def record(decision):
    log.info('模型预算 %s', json.dumps(asdict(decision), ensure_ascii=False))
    from src.qa import conversation_context
    context = conversation_context.active.get()
    if context is not None:
        context.stats['model_budget'] = asdict(decision)
        context.stats['model_budget_notes'] = decision.notes()


def limit_payload(payload, budget):
    # Count final wire input, including native tools and protocol-added metadata.
    key = 'max_output_tokens' if 'max_output_tokens' in payload else 'max_tokens'
    requested = payload[key]
    content = {k: v for k, v in payload.items() if k not in (key, 'stream', 'temperature', 'store', 'model')}
    decision = budget.decide(requested, estimate(content))
    payload[key] = decision.actual_output
    record(decision)
    return decision


def limit_messages(messages, kwargs, budget):
    requested = kwargs.get('max_tokens', 1024)
    decision = budget.decide(requested, estimate(messages))
    record(decision)
    return {**kwargs, 'max_tokens': decision.actual_output}


def truncation_message(decision=None):
    detail = decision.notes() if decision else '请查看本轮实际输出额度和模型能力配置'
    return f'模型输出达到输出上限，回答未生成或已截断。{detail}。可减少输入资料或调整对应预算；调大系统输出上限不能突破模型能力和剩余窗口。'
