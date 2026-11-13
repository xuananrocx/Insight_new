"""MCP-only local relevance assessment. Never downloads or calls a chat model."""
from __future__ import annotations

import math
from pathlib import Path
import threading
import time

from src.mcp import store
from src.qa import tool_navigation

class RelevanceUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


MODEL = 'BAAI/bge-reranker-v2-m3'
_lock = threading.Lock()
_model = None
_failed_until = 0.


def model_path():
    import os
    from huggingface_hub import try_to_load_from_cache
    def complete(path):
        return ((path/'config.json').is_file() and (path/'tokenizer.json').is_file()
                and any((path/name).is_file() for name in ('model.safetensors', 'pytorch_model.bin')))
    # Honor configured cache first; also recognize the standard HF user cache.
    roots = [None, str(Path.home()/'.cache/huggingface/hub')]
    for root in roots:
        cached = try_to_load_from_cache(MODEL, 'config.json', cache_dir=root)
        if isinstance(cached, str):
            path = Path(cached).parent
            if complete(path):
                return path
    # The offline Linux bundle mounts the same model beside HF_HOME, outside its cache.
    hf_home = os.environ.get('HF_HOME')
    if hf_home:
        bundled = Path(hf_home).parent/'reranker-torch'
        if complete(bundled):
            return bundled
    raise FileNotFoundError('Local MCP reranker is not deployed')


def score(query, texts):
    global _model, _failed_until
    # No queue behind another inference: don't use up the entire MCP deadline.
    if not _lock.acquire(blocking=False):
        raise RelevanceUnavailable('reranker_busy')
    try:
        if _model is None:
            if time.monotonic() < _failed_until:
                raise RelevanceUnavailable('reranker_cooling_down')
            try:
                from sentence_transformers import CrossEncoder
                _model = CrossEncoder(str(model_path()), device='cpu', max_length=512, local_files_only=True)
            except Exception:
                _failed_until = time.monotonic()+60
                raise
        import torch
        raw = _model.predict([(query, text) for text in texts], batch_size=4,
                             activation_fct=torch.nn.Identity(), show_progress_bar=False)
        return [1/(1+math.exp(-max(-80., min(80., float(v))))) for v in raw]
    finally:
        _lock.release()


def select(entries, maximum):
    """Bound inference, keep diverse sources, deduplicate only within a source."""
    groups, seen = {}, set()
    for hit in entries:
        fingerprint = (hit['file_id'], ' '.join(hit['text'].split()))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        groups.setdefault(hit['file_id'], []).append(hit)
    chosen = []
    while groups and len(chosen) < maximum:
        for key in list(groups):
            chosen.append(groups[key].pop(0))
            if not groups[key]:
                del groups[key]
            if len(chosen) >= maximum:
                break
    return chosen


def assess(kb, query, entries):
    cfg = store.config()
    meta = {'enabled': cfg.rerank_enabled, 'status': 'disabled', 'candidate_count': len(entries),
            'scored_count': 0, 'accepted_count': None, 'score_is_probability': False}
    if not cfg.rerank_enabled or not entries:
        if cfg.rerank_enabled:
            meta['status'] = 'no_candidates'
        return entries, meta
    pool = select(entries, cfg.rerank_candidates)
    start = time.monotonic()
    scored = []
    failure = None
    with tool_navigation.timed(kb, 'mcp_relevance'):
        for index in range(0, len(pool), 4):
            kb.check()
            if time.monotonic()-start >= cfg.rerank_seconds:
                failure = 'time_budget'
                break
            batch = pool[index:index+4]
            # Bound what is assessed and tell the caller this is excerpt relevance.
            texts = [hit['text'][:2500] for hit in batch]
            try:
                scores = score(query, texts)
                if len(scores) != len(batch) or not all(math.isfinite(v) and 0 <= v <= 1 for v in scores):
                    raise ValueError('invalid_reranker_output')
            except Exception as exc:
                from fastapi import HTTPException
                from src.qa.trace import PipelineCancelled
                if isinstance(exc, (HTTPException, PipelineCancelled)):
                    raise
                failure = (exc.code if isinstance(exc, RelevanceUnavailable) else
                           'reranker_not_deployed' if isinstance(exc, FileNotFoundError) else
                           'invalid_reranker_output' if isinstance(exc, ValueError) else 'reranker_failed')
                break
            kb.check()
            scored.extend({**hit, 'relevance_score': value} for hit, value in zip(batch, scores))
    accepted = sorted((hit for hit in scored if hit['relevance_score'] >= cfg.rerank_min_score),
                      key=lambda hit: -hit['relevance_score'])
    meta.update(status='partial' if failure else 'assessed', scored_count=len(scored),
                selected_count=len(pool), unassessed_count=len(entries)-len(scored),
                accepted_count=len(accepted), threshold=cfg.rerank_min_score,
                score_scope='original_query_vs_first_2500_chars; model_pair_limit_512_tokens; not_fact_verification',
                elapsed_ms=round((time.monotonic()-start)*1000, 3))
    if failure:
        meta.update(reason=failure, recovery='Narrow to a document or original-language query; administrator checks local reranker.')
        meta['retryable'] = failure in ('reranker_busy', 'reranker_cooling_down', 'time_budget')
        if failure == 'reranker_busy':
            meta.update(retry_after_seconds=1, recovery='Retry after the active assessment, or narrow to a document; do not treat unassessed candidates as relevant evidence.')
        if not scored:
            meta['status'] = 'unavailable'
            # Availability fallback is explicit; never mark these as assessed relevance.
            return entries, meta
    # Unassessed candidates are not silently mixed into the filtered evidence list.
    return accepted, meta
