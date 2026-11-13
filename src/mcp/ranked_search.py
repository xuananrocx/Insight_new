"""Bounded MCP candidate fusion. No chat calls or implicit query rewriting."""
from __future__ import annotations

import re
from collections import Counter

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db as db
from src.qa import bm25_index, indexed_reading, tool_navigation
from src.knowledge import reading_index
from src.mcp import relevance, store

CANDIDATES = 120


def valid_candidates(kb, groups, owners):
    """Use one eligibility rule for coverage and fusion; summaries are navigation."""
    ids = list(dict.fromkeys(h.get('id') for group in groups for h in group if h.get('id') in owners))
    kb.check()
    with tool_navigation.timed(kb, 'mcp_live_candidates'):
        live = kb.live_chunks(ids) if ids else {}
    valid = {}
    for cid, raw in live.items():
        file = owners.get(cid)
        if (not file or raw.get('chunk_type') == 'summary'
                or raw.get('file_id') not in (None, file['id'])
                or raw.get('content_hash') not in (None, file['content_hash'])):
            continue
        valid[cid] = {**raw, 'file_id': file['id']}
    return valid


def search(kb, arguments, variants):
    queries = list(dict.fromkeys([arguments['query'], *variants]))
    mode = arguments.get('match_mode', 'related')
    files = [kb.file(arguments['document_id'])] if 'document_id' in arguments else [f for f in kb.files() if f['status'] == 'done']
    owners = {cid: f for f in files for cid in kb.ids(f)}
    collection = db.get_kb(kb.kb_id)['collection_name']
    probed = []
    probe_eligible = 0
    groups, paths, warnings = [], [], []
    vectors = []
    # Title matches select extra document-scoped candidates; a name alone is never evidence.
    terms = set(re.findall(r'[a-z0-9]{3,}', ' '.join(queries).lower()))
    title_files = sorted((f for f in files if terms & set(re.findall(r'[a-z0-9]{3,}', f['relative_path'].lower()))),
                         key=lambda f: (-len(terms & set(re.findall(r'[a-z0-9]{3,}', f['relative_path'].lower()))), f['id']))[:3]
    if mode in ('semantic', 'hybrid', 'related') and not (mode == 'related' and 'document_id' in arguments):
        stage = 'embedding'
        try:
            with tool_navigation.timed(kb, 'mcp_embedding'):
                vectors, _ = llm_client.get_client().embed(queries)
            threshold = float((settings.config.get('qa') or {}).get('similarity_threshold', .3) or 0)
            stage = 'vector_query'
            with tool_navigation.timed(kb, 'mcp_vectors'):
                for vector in vectors:
                    kb.check()
                    where = {'file_id': files[0]['id']} if 'document_id' in arguments else None
                    hits = vector_store.query_by_embedding(vector, k=CANDIDATES, where=where, collection_name=collection)
                    groups.append([h for h in hits if h.get('score', 0) >= threshold])
                    if 'vector' not in paths:
                        paths.append('vector')
                # Only explicit title matches justify extra targeted retrieval.
                for file in title_files if 'document_id' not in arguments else []:
                    kb.check()
                    hits = vector_store.query_by_embedding(vectors[0], k=16, where={'file_id': file['id']}, collection_name=collection)
                    groups.append([h for h in hits if h.get('score', 0) >= threshold])
                    if 'vector' not in paths:
                        paths.append('vector')
                if 'document_id' not in arguments and vectors and store.config().rerank_enabled:
                    current = valid_candidates(kb, groups, owners)
                    represented = {h['file_id'] for h in current.values()}
                    # Bounded opportunities for sources absent from global candidates.
                    # No file-type/name whitelist; reranking decides relevance.
                    missing = sorted((f for f in files if f['id'] not in represented and kb.ids(f)),
                                     key=lambda f: (len(kb.ids(f)), str(f['id'])))
                    probe_eligible = len(missing)
                    for file in missing[:8]:
                        kb.check()
                        hits = vector_store.query_by_embedding(vectors[0], k=4, where={'file_id': file['id']}, collection_name=collection)
                        # At most one bounded refill if summaries/stale chunks consume slots.
                        if len(valid_candidates(kb, [hits], owners)) < min(4, len(kb.ids(file))):
                            kb.check()
                            hits = vector_store.query_by_embedding(vectors[0], k=16, where={'file_id': file['id']}, collection_name=collection)
                        groups.append(hits)
                        probed.append(file['id'])
        except Exception as exc:
            from fastapi import HTTPException
            from src.qa.trace import PipelineCancelled
            if isinstance(exc, (HTTPException, PipelineCancelled)):
                raise
            kb.check()
            warning = {'path': 'vector', 'stage': stage, 'status': 'unavailable', 'reason': type(exc).__name__,
                       'action': 'Check embedding availability; keyword fallback does not validate semantic retrieval.'}
            if isinstance(exc, llm_client.EmbeddingLoadError):
                warning.update(code=exc.code, retry_after_seconds=exc.retry_after_seconds,
                               delay_is_advisory=True)
            warnings.append(warning)
    # related document queries historically mean lexical. Global related is hybrid.
    lexical = mode in ('keyword', 'hybrid', 'related') or not paths
    if lexical:
        with tool_navigation.timed(kb, 'mcp_keywords'):
            try:
                for query in queries:
                    kb.check()
                    if 'document_id' in arguments:
                        group = bm25_index.query_enhanced(query, CANDIDATES, kb.kb_id, allowed_ids=set(owners))
                    else:
                        group = bm25_index.query_enhanced(query, CANDIDATES, kb.kb_id)
                    groups.append(group)
                paths.append('keyword')
            except Exception as exc:
                from fastapi import HTTPException
                from src.qa.trace import PipelineCancelled
                if isinstance(exc, (HTTPException, PipelineCancelled)):
                    raise
                kb.check()
                warnings.append({'path': 'keyword', 'status': 'unavailable', 'reason': type(exc).__name__})
    live = valid_candidates(kb, groups, owners)
    scores, hits_by_id = Counter(), {}
    for group in groups:
        seen = set()
        for rank, hit in enumerate(group):
            cid = hit.get('id')
            raw = live.get(cid)
            if cid in seen or not raw:
                continue
            seen.add(cid)
            scores[cid] += 1/(60+rank+1)
            hits_by_id[cid] = raw
    ordered = sorted(scores, key=lambda cid: (-scores[cid], str(cid)))
    selected, deferred, counts = [], [], Counter()
    best = scores[ordered[0]] if ordered else 0
    fingerprints = set()
    for cid in ordered:
        hit = hits_by_id[cid]
        # Identical text in different documents/locations is not automatically duplicate evidence.
        fingerprint = (hit['file_id'], hit.get('section_index'), hit.get('chunk_index', cid), hit['text'])
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        if counts[hit['file_id']] < 2 and scores[cid] >= best*.6:
            selected.append(hit)
            counts[hit['file_id']] += 1
        else:
            deferred.append(hit)
    # Assess a bounded, source-diverse pool before reducing the returned list.
    entries = selected+deferred
    assessment = {'status': 'not_applicable', 'enabled': False}
    if mode in ('semantic', 'hybrid', 'related'):
        entries, assessment = relevance.assess(kb, arguments['query'], entries)
    entries = entries[:CANDIDATES]
    versions = []
    for file in files:
        info = reading_index.info(file)
        versions.append({'id': file['id'], 'version': file['content_hash'], 'artifact': info['artifact'] if info else None})
    snapshot = {'kind': 'mcp_ranked', 'entries': entries, 'versions': versions,
        'mode': mode, 'queries': queries, 'warnings': warnings, 'paths': paths,
        'document_id': arguments.get('document_id'), 'candidate_pool_size': len(ordered),
        'catalog_candidates': [kb.brief(f) for f in title_files]}
    snapshot.update(relevance=assessment, source_probes=len(probed), probe_eligible=probe_eligible,
                    candidate_counts={'raw_hits': sum(map(len, groups)), 'valid_unique_body_chunks': len(live),
                                      'fused_chunks': len(ordered), 'returned_candidates': len(entries)})
    key = tool_navigation.save(kb, snapshot)
    return page(kb, key, kb.search_pages[key], arguments.get('offset', 0), arguments.get('limit', 8))


def page(kb, key, snapshot, offset, limit):
    tool_navigation.validate_snapshot(kb, snapshot)
    evidence = []
    for hit in snapshot['entries'][offset:offset+limit]:
        kb.check()
        if evidence and min(2500, len(hit['text'])) > kb.max_chars-kb.used_chars:
            break
        file = kb.file(hit['file_id'])
        ev = kb.evidence(hit, file)
        if ev.get('error'):
            break
        with tool_navigation.timed(kb, 'mcp_source_location'):
            location = indexed_reading.locate(kb, file, ev['text'])
        ev['relevance_status'] = 'assessed' if 'relevance_score' in hit else 'unassessed'
        ev['location'] = location
        if location['status'] == 'exact':
            ev['read_ref'] = location['candidates'][0]['read_ref']
        else:
            ev['reading_fallback'] = location['status']
        evidence.append(ev)
    end = offset+len(evidence)
    tool_navigation.validate_snapshot(kb, snapshot)
    result = {'evidence': evidence, 'status': 'found' if evidence else 'no_match',
        'next_cursor': tool_navigation.cursor(kb, key, end, limit) if end < len(snapshot['entries']) else None,
        'matching_chunks': len(snapshot['entries']), 'query_variants_used': snapshot['queries'],
        'retrieval_warnings': snapshot['warnings'], 'catalog_candidates': snapshot['catalog_candidates'],
        'relevance': snapshot.get('relevance', {'status': 'not_assessed'}),
        'search_scope': {'kind': 'document' if snapshot['document_id'] else 'knowledge_base',
                         'match_mode': snapshot['mode'], 'executed_paths': snapshot['paths'],
                         'degraded': bool(snapshot['warnings'])},
        'coverage': {'source': 'bounded_live_candidates', 'scan_complete': None, 'exhaustive': False,
                     'candidate_limited': True, 'candidate_pool_size': snapshot['candidate_pool_size'],
                     'candidate_counts': snapshot.get('candidate_counts', {}),
                     'targeted_source_probes': snapshot.get('source_probes', 0),
                     'unprobed_sources': max(0, snapshot.get('probe_eligible', 0)-snapshot.get('source_probes', 0)),
                     'parse_completeness': 'unknown'}}
    result['retrieval_guidance'] = ('结果是有限候选，需阅读原文判断是否回答问题。未匹配或相关性不足时，可查文件名/目录、读原文，'
        '或显式提供原文语言的 query_variants；不能将少量结果或 no_match 视为知识库无答案。')
    if snapshot['warnings']:
        result['status'] = 'partial' if evidence else 'no_match'
        if not snapshot['paths']:
            result.update(error='index_unavailable', message='本次所有检索路径不可用，不能视为知识库无答案')
    if result['relevance']['status'] in ('partial', 'unavailable'):
        result['status'] = 'partial'
        result['retrieval_guidance'] += ' 本次相关性评估未完成；未评估的候选不能用于证明没有答案。'
    for ev, hit in zip(evidence, snapshot['entries'][offset:end]):
        if 'relevance_score' in hit:
            ev['relevance_score'] = hit['relevance_score']
    if result['relevance']['status'] == 'unavailable':
        result['unassessed_candidates'] = result['evidence']
        result['evidence'] = []
        result['retrieval_guidance'] += ' unassessed_candidates 仅供定位原文，不是已通过相关性筛选的证据。'
    result['search_scope']['degraded'] = bool(snapshot['warnings']) or result['relevance']['status'] in ('partial', 'unavailable')
    return result
