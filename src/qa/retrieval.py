"""Evidence retrieval shared by basic, deep and deep_ai; no chat model calls."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Callable

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db
from src.qa import bm25_index, reranker
from src.qa.trace import PipelineCancelled, TraceCollector

logger = logging.getLogger(__name__)


def _bounded_rerank(question, hits, qa, trace, aspects=None):
    """Keep recall broad, but bound CPU inference separately from recall size."""
    cfg = qa.get("retrieval", {})
    limit = max(4, min(40, int(cfg.get("rerank_candidates", 24))))
    batch_size = max(2, min(8, int(cfg.get("rerank_batch_size", 4))))
    seconds = max(1, min(60, float(cfg.get("rerank_seconds", 20))))
    pool = select_evidence(hits, limit, aspects)
    rerank_cfg = qa.get("rerank", {})
    ranked = []
    scored_ids = set()
    deadline = time.monotonic() + seconds
    with trace.stage("rerank", "分批重排相关资料") as stage:
        for offset in range(0, len(pool), batch_size):
            trace.cancel_check()
            if time.monotonic() >= deadline:
                break
            batch = pool[offset:offset + batch_size]
            # The shared legacy reranker bypasses scoring for one document.
            # Pair a final singleton with an already scored anchor, then discard it.
            inputs = batch if len(batch) > 1 else [*batch, pool[0]]
            originals = {h["id"]: h["text"] for h in inputs}
            candidates = [{**h, "text": "\n".join(str(h.get(key) or "") for key in
                          ("title", "section_label", "version", "text"))} for h in inputs]
            try:
                output = reranker.rerank(question, candidates, top_k=len(candidates),
                    model_name=rerank_cfg.get("model_name", reranker.DEFAULT_MODEL),
                    backend=rerank_cfg.get("backend", "auto"))
            except PipelineCancelled:
                raise
            except Exception as exc:
                logger.warning("分批重排失败，保留融合结果: %s", exc)
                break
            trace.cancel_check()
            wanted = {h["id"] for h in batch}
            for hit in output:
                if hit["id"] in wanted and hit["id"] not in scored_ids:
                    ranked.append({**hit, "text": originals[hit["id"]]})
                    scored_ids.add(hit["id"])
        ranked.sort(key=lambda h: h.get("rerank_score", h.get("score", 0)), reverse=True)
        # Never drop unscored evidence when the CPU budget expires.
        remaining = [h for h in pool if h["id"] not in scored_ids]
        stage.set(count=len(ranked), status="partial" if remaining else "ok",
                  notes=f"{len(hits)} 个召回候选，精选 {len(pool)} 个；完成重排 {len(ranked)} 个，"
                        f"其余保留融合排序；每批最多 {batch_size} 个")
    return ranked + remaining


def _terms(text: str) -> set[str]:
    return {t for t in bm25_index._tokenize(text) if len(t) > 1}


def expand_aliases(question: str) -> str:
    aliases = settings.config.get("qa", {}).get("retrieval", {}).get("aliases", {})
    additions = []
    if isinstance(aliases, dict):
        for term, values in aliases.items():
            values = values if isinstance(values, list) else [values]
            group = [str(term), *[v for v in values if isinstance(v, str)]]
            if any(v and v.lower() in question.lower() for v in group):
                additions.extend(group)
    return (question + " " + " ".join(dict.fromkeys(additions))[:600]).strip()


def _signature(hit: dict) -> tuple[str, set[str]]:
    text = re.sub(r"\s+", "", hit.get("text", ""))[:2000]
    width = min(12, len(text))
    return text, {text[i:i + width] for i in range(len(text) - width + 1)} if text else set()


def _similar(a: dict, b: dict, signatures: dict) -> float:
    left, left_spans = signatures[id(a)]
    right, right_spans = signatures[id(b)]
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # Overlapping chunks from the same document should not crowd out other evidence.
    if a.get("source_path") != b.get("source_path"):
        return 0.0
    # Character-span overlap stays bounded even for long, repetitive JSON/code.
    # SequenceMatcher with autojunk=False was taking ~22s for 80 real candidates.
    return 2 * len(left_spans & right_spans) / (len(left_spans) + len(right_spans))


def select_evidence(hits: list[dict], top_k: int, aspects: list[str] | None = None) -> list[dict]:
    """Keep relevance while rewarding new question aspects and source diversity."""
    pool = [(i, h) for i, h in enumerate(hits) if h.get("text")]
    selected: list[dict] = []
    sources: Counter = Counter()
    aspect_terms = [_terms(a) for a in (aspects or [])[:6]]
    covered: set[int] = set()
    matches = {}
    signatures = {id(hit): _signature(hit) for _, hit in pool}
    overlaps = {}

    def overlap_with(hit, prior):
        key = (id(hit), id(prior))
        if key not in overlaps:
            overlaps[key] = _similar(hit, prior, signatures)
        return overlaps[key]

    for _, hit in pool:
        words = _terms(hit["text"]) if aspect_terms else set()
        matches[id(hit)] = {i for i, terms in enumerate(aspect_terms)
                            if terms and len(words & terms) / len(terms) >= 0.35}
    while pool and len(selected) < top_k:
        options = []
        for rank, hit in pool:
            overlap = max((overlap_with(hit, prior) for prior in selected), default=0.0)
            if overlap >= 0.82:
                continue
            matched = matches[id(hit)]
            score = 1.0 / (rank + 2) + 0.16 * len(matched - covered)
            score -= 0.06 * sources[hit.get("source_path", "")] + 0.25 * overlap
            options.append((score, -rank, hit, matched))
        if not options:
            break
        _, _, chosen, matched = max(options, key=lambda item: (item[0], item[1]))
        selected.append(chosen)
        covered.update(matched)
        sources[chosen.get("source_path", "")] += 1
        pool = [(rank, h) for rank, h in pool if h is not chosen]
    return selected


def expand_context(hits: list[dict], collection_name: str, trace: TraceCollector) -> list[dict]:
    from src.qa.rag import _join_with_overlap

    expanded = []
    with trace.stage("context_merge", "补全同章节上下文") as stage:
        count = 0
        for hit in hits:
            trace.cancel_check()
            try:
                neighbors = vector_store.get_context_chunks(hit, collection_name)
            except Exception as exc:
                logger.warning("上下文补全失败，保留原片段: %s", exc)
                neighbors = []
            if not neighbors:
                expanded.append(hit)
                continue
            # Verify metadata even for adapters returning unexpected rows.
            matching = [n for n in neighbors if all(n.get(k) == hit.get(k) for k in
                        ("source_path", "content_hash", "section_index"))]
            if not any(n.get("id") == hit.get("id") for n in matching):
                expanded.append(hit)
                continue
            center = hit.get("chunk_index", 0)
            kept = {center: hit}
            chars = len(hit["text"])
            # Grow contiguously from the actual hit, within an explicit per-source budget.
            indexed = {n["chunk_index"]: n for n in matching}
            for direction in (-1, 1):
                for distance in (1, 2):
                    index = center + direction * distance
                    neighbor = indexed.get(index)
                    if not neighbor or chars + len(neighbor["text"]) > 6000:
                        break
                    kept[index] = neighbor
                    chars += len(neighbor["text"])
            text = ""
            for index in sorted(kept):
                piece = kept[index]["text"]
                combined = _join_with_overlap(text, piece)
                text = text + "\n\n" + piece if text and combined == text + piece else combined
            added = len(kept) - 1
            count += bool(added)
            expanded.append({**hit, "text": text, "merged_chunks": added})
        stage.set(count=count, notes="按来源、文件版本及章节补全；旧索引无需重建")
    return select_evidence(expanded, len(hits))


def search(
    question: str, top_k: int | None, kb_scope: str, trace: TraceCollector,
    *, deep: bool = False, queries: list[str] | None = None,
    aspects: list[str] | None = None, prior_hits: list[dict] | None = None,
    ranking_question: str | None = None,
    on_evidence: Callable[[list[dict], str], None] | None = None,
) -> tuple[list[dict], str]:
    """Bounded multi-query hybrid search. Raw sources are rechecked in Chroma."""
    qa = settings.config.get("qa", {})
    k = min(20, max(1, top_k or qa.get("top_k", 5)))
    candidate_k = min(80, max(20, k * 4))
    kb = metadata_db.get_kb(kb_scope)
    if not kb:
        raise ValueError(f"知识库不存在: {kb_scope}")
    collection = kb["collection_name"]
    raw_queries = list(dict.fromkeys([question, *(queries or [])]))[:3]
    queries = [expand_aliases(q[:4000]) for q in raw_queries if q.strip()]
    with trace.stage("embedding", "问题向量化") as stage:
        vectors, provider = llm_client.get_client().embed(queries)
        stage.set(count=len(vectors))
    ranked_lists = []
    threshold = float(qa.get("similarity_threshold", 0.3) or 0)
    with trace.stage("vector_retrieval", "扩大语义召回") as stage:
        for vector in vectors:
            trace.cancel_check()
            # Oversample before removing legacy summary chunks (raw chunks may have no type).
            hits = vector_store.query_by_embedding(vector, k=candidate_k * 2, collection_name=collection)
            ranked_lists.append([h for h in hits if h.get("chunk_type") != "summary"
                                 and h.get("score", 0) >= threshold][:candidate_k])
        stage.set(count=sum(map(len, ranked_lists)))
    with trace.stage("bm25", "关键词与接口名召回") as stage:
        lexical = []
        if qa.get("hybrid_search", True):
            for query in queries:
                trace.cancel_check()
                try:
                    lexical.append(bm25_index.query_enhanced(query, candidate_k, kb_scope))
                except Exception as exc:
                    logger.warning("关键词召回失败: %s", exc)
                    stage.set(status="partial", notes="关键词索引不可用，采用语义结果")
            # A removed document or stale BM25 entry must never become answer evidence.
            ids = list(dict.fromkeys(h["id"] for group in lexical for h in group))
            live = vector_store.get_chunks_by_ids(ids, collection_name=collection) if ids else {}
            lexical = [[{**h, **live[h["id"]]} for h in group if h["id"] in live
                        and live[h["id"]].get("chunk_type") != "summary"] for group in lexical]
        ranked_lists.extend(lexical)
        stage.set(count=sum(map(len, lexical)))
    with trace.stage("fusion", "融合候选资料") as stage:
        scores: Counter = Counter()
        by_id = {}
        for group in [*ranked_lists, prior_hits or []]:
            seen = set()
            for rank, hit in enumerate(group):
                cid = hit.get("id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                scores[cid] += 1 / (60 + rank + 1)
                by_id[cid] = hit
        hits = [{**by_id[cid], "score": score, "rrf_score": score}
                for cid, score in scores.most_common(candidate_k * 2)]
        stage.set(count=len(hits), notes="保留两路候选后再精排")
    # Publish usable raw evidence before any slow CPU model call. A timeout in
    # reranking must not erase sources that have already been successfully found.
    if on_evidence and hits:
        on_evidence(hits[:k], provider)
    if deep and hits:
        from src.qa.rag import _expand_with_concepts
        with trace.stage("concept_expansion", "扩展相关概念资料") as stage:
            try:
                hits, concepts = _expand_with_concepts(question, hits, kb_scope, collection, vectors[0])
                stage.set(count=len(concepts))
            except PipelineCancelled:
                raise
            except Exception as exc:
                stage.set(status="partial", notes=str(exc)[:200])
    rerank_cfg = qa.get("rerank", {})
    if deep and rerank_cfg.get("enabled", True) and len(hits) > 1:
        hits = _bounded_rerank(ranking_question or question, hits, qa, trace, aspects)
    else:
        with trace.stage("rerank", "相关性重排") as stage:
            stage.set(status="skipped", count=len(hits), notes="basic 档不重排" if not deep else "无需重排")
    with trace.stage("evidence_selection", "去重并覆盖问题要点") as stage:
        hits = select_evidence(hits, k, aspects)
        stage.set(count=len(hits))
    if deep:
        hits = expand_context(hits, collection, trace)
    if on_evidence and hits:
        on_evidence(hits, provider)
    return hits, provider
