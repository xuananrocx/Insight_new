"""Trace collector for RAG pipeline visibility.

Records per-stage timing, candidate snapshots, and status flags so the
frontend can render a DeepSeek-R1-style "thinking" panel above the answer.
"""
from __future__ import annotations

import time
from typing import Any, Callable


class PipelineCancelled(Exception):
    """Cooperative cancellation at retrieval stage boundaries."""


class TraceCollector:
    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self.cancel_check: Callable[[], None] = lambda: None

    def stage(self, name: str, label: str) -> _StageCtx:
        return _StageCtx(self, name, label)

    def to_list(self) -> list[dict[str, Any]]:
        return self.stages

    def on_stage_complete(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback fired when each stage exits.

        Used by `ask_stream` to incrementally emit SSE events without
        duplicating the 7-stage pipeline body.
        """
        self._listeners.append(cb)


class _StageCtx:
    def __init__(self, col: TraceCollector, name: str, label: str) -> None:
        self._col = col
        self._name = name
        self._label = label
        self._t0 = 0.0
        self._data: dict[str, Any] = {}

    def __enter__(self) -> _StageCtx:
        self._col.cancel_check()
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc_info) -> bool:
        duration_ms = round((time.perf_counter() - self._t0) * 1000, 1)
        payload = {
            "stage": self._name,
            "label": self._label,
            "duration_ms": duration_ms,
            **self._data,
        }
        self._col.stages.append(payload)
        for cb in self._col._listeners:
            try:
                cb(payload)
            except Exception as e:
                # 监听器是 side channel，不应影响主流程；只 debug 记录
                import logging
                logging.getLogger(__name__).debug(f"trace listener 抛错: {e}", exc_info=True)
        if exc_info[0] is None:
            self._col.cancel_check()
        return False

    def set(self, **kwargs: Any) -> None:
        self._data.update(kwargs)


def make_candidate(
    *,
    title: str | None,
    source_name: str | None,
    score: float | None,
    score_type: str,
    text: str | None,
    preview_len: int = 80,
) -> dict[str, Any]:
    preview = (text or "").strip().replace("\n", " ")[:preview_len]
    return {
        "title": (title or "").strip()[:120] or None,
        "source_name": source_name or None,
        "score": round(float(score), 4) if score is not None else None,
        "score_type": score_type,
        "preview": preview or None,
    }
