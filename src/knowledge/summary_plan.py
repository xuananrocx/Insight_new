"""Local, deterministic planning and bounded overview evidence; no model calls."""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter

from fastapi import HTTPException
from src.db import metadata_db as db
from src.knowledge import ai_summarizer as summary, reading_index
from src.knowledge.chunker import Chunk

VERSION = 'overview-v1'
AUTO_CALLS = 12
DEFAULT_BUDGET = 40


def read_chunks(file, collection, check=lambda: None):
    with reading_index.open_index(file) as index:
        if index is not None:
            chunks = []
            for row in index.execute('SELECT id,label,text FROM sections ORDER BY id'):
                check()
                if row['text'].strip():
                    chunks.append(Chunk(row['text'], dict(section_index=row['id'], section_label=row['label'])))
            return chunks
    ids = json.loads(file.get('chunk_ids_json') or '[]')
    chunks = summary._fetch_chunks_by_ids(collection, ids)
    if not chunks or len(chunks) != len(ids):
        raise ValueError('文档原文片段缺失，请检查文档索引')
    return summary._without_source_overlap(chunks)


def cached_overview(file):
    with db.get_cursor() as cur:
        row = cur.execute('SELECT * FROM summary_overviews WHERE file_id=? AND content_hash=? AND version=?',
                          (file['id'], file['content_hash'], VERSION)).fetchone()
    return dict(row) if row else None


def preview(kb_id, full_ids=()):
    from src.knowledge import summary_tasks as jobs
    from src.core.summary_policy import effective_policy
    policy = effective_policy()
    jobs.init_schema()
    kb = db.get_kb(kb_id)
    if not kb:
        raise HTTPException(404, '知识库不存在')
    files = db.list_files_in_kb(kb_id, status='done')
    if not files:
        raise HTTPException(400, '知识库没有已入库文档')
    if set(full_ids) - {f['id'] for f in files}:
        raise HTTPException(400, '选择的文档已不存在，请刷新预览')
    metas = {m['file_id']: m for m in db.list_document_meta_by_kb(kb_id)}
    documents = []
    for file in files:
        row = dict(file_id=file['id'], name=file['relative_path'], content_hash=file['content_hash'],
                   chars=0, segments=0, cached_segments=0, estimated_calls=0, full_calls=0, error=None)
        if jobs.valid_summary(file, metas.get(file['id'])):
            row['action'] = 'reuse'
        elif cached_overview(file) and file['id'] not in full_ids:
            row['action'] = 'reuse_overview'
        else:
            try:
                chunks = read_chunks(file, kb['collection_name'])
                if not chunks:
                    raise ValueError('文档无可读取正文')
                segments = summary._split_chunks_by_sections(chunks, summary._segment_chars())
                row['chars'] = sum(len(c.text) for c in chunks)
                row['segments'] = n = len(segments)
                # Same keys as worker: cached map results survive planning and retries.
                signature = jobs.provider_signature()
                if n > 1:
                    with db.get_cursor() as cur:
                        for segment in segments:
                            key = jobs.digest(signature + ':' + str(file['id']) + ':' + file['content_hash'] + ':' + '\n\n'.join(c.text for c in segment))
                            if cur.execute('SELECT 1 FROM summary_checkpoints WHERE kb_id=? AND cache_key=?', (kb_id, key)).fetchone():
                                row['cached_segments'] += 1
                row['full_calls'] = 1 if n == 1 else n - row['cached_segments'] + math.ceil(n / 8) + 1
                row['action'] = 'full' if file['id'] in full_ids or row['full_calls'] <= policy.auto_full_calls else 'overview'
                row['estimated_calls'] = row['full_calls'] if row['action'] == 'full' else 1
            except Exception:
                row.update(action='unavailable', error='无法读取完整索引，请检查文档索引后重试')
        documents.append(row)
    metas = {m['file_id']: m for m in db.list_document_meta_by_kb(kb_id)}
    known_notes = []
    for file, doc in zip(files, documents):
        if doc['action'] == 'reuse':
            known_notes.append((file['relative_path'], metas[file['id']]['summary']))
        elif doc['action'] == 'reuse_overview':
            known_notes.append((file['relative_path'], cached_overview(file)['summary']))
    # Follow the same grouping and cache keys as execution. A missing group's
    # output length is unknown, so later layers remain an estimate.
    from src.core.summary_policy import generation_signature
    remaining_notes = known_notes if len(known_notes) == len(files) else []
    group_calls, cached_groups = 0, 0
    while sum(len(n)+len(label)+40 for label,n in remaining_notes) > policy.reduce_chars:
        reduced, missing = [], False
        for i, group in enumerate(summary.reduce_groups(remaining_notes, policy.reduce_chars), 1):
            if sum(len(n) + len(label) + 40 for label, n in group) <= 1200:
                reduced.append((f'汇总组 {i}', '\n\n'.join(f'{label}: {note}' for label, note in group)))
                continue
            text = json.dumps([generation_signature(), group], ensure_ascii=False, sort_keys=True)
            key = jobs.digest('reduce-v1:' + jobs.provider_signature() + ':' + text)
            with db.get_cursor() as cur:
                row = cur.execute('SELECT note FROM summary_checkpoints WHERE kb_id=? AND cache_key=? AND created_at>=?',
                                  (kb_id, key, time.time() - policy.checkpoint_retention_days * 86400)).fetchone()
            if row:
                cached_groups += 1
                reduced.append((f'汇总组 {i}', row[0]))
            else:
                group_calls += 1
                missing = True
        if missing:
            break
        if sum(len(n)+len(label)+40 for label,n in reduced) >= sum(len(n)+len(label)+40 for label,n in remaining_notes):
            break
        remaining_notes = reduced
    calls = sum(d['estimated_calls'] for d in documents) + group_calls + 1
    if len(known_notes) != len(files) and len(files) > 1:
        calls += math.ceil((len(files) - len(known_notes)) / 8)
    payload = dict(documents=documents, estimated_calls=calls, default_budget=DEFAULT_BUDGET,
                   blocked=any(d['error'] for d in documents), full_file_ids=sorted(full_ids),
                   cached_groups=cached_groups, request_limit=policy.request_limit, request_limit_max=1000,
                   task_timeout_seconds=policy.task_timeout_seconds, policy=policy.model_dump())
    # Checkpoints change while jobs run; confirmation must describe the current plan.
    payload['token'] = jobs.digest(json.dumps(payload, sort_keys=True) + jobs.provider_signature())
    return payload


def overview_evidence(chunks, limit=18000):
    """Uniform windows across the entire indexed text, with explicit coverage.

    Index structures (when available) are summarized separately by the caller.
    This is positional sampling, never a claim of statistical representation.
    """
    total = sum(len(c.text) for c in chunks)
    width = min(1500, limit)
    count = max(1, min(limit // width, math.ceil(total / width)))
    starts = sorted({round(i * max(0, total - width) / max(1, count - 1)) for i in range(count)})
    samples = []
    for start in starts:
        offset, parts = 0, []
        for chunk in chunks:
            end = offset + len(chunk.text)
            if end > start and offset < start + width:
                parts.append(chunk.text[max(0, start-offset):max(0, min(len(chunk.text), start+width-offset))])
            offset = end
            if offset >= start + width:
                break
        samples.append(f'[正文字符位置 {start}:{min(total, start+width)}]\n' + ''.join(parts))
    covered = 0
    last = 0
    for start in starts:
        end = min(total, start + width)
        covered += max(0, end - max(last, start))
        last = end
    coverage = dict(total_chars=total, sampled_chars=covered, windows=len(starts),
                    method='按正文位置均匀选取窗口，非全文覆盖；不保证覆盖所有类别或异常')
    return coverage, '\n\n'.join(samples)


def structural_stats(file, chunks=()):
    """Reuse local parser's object index; do not parse untrusted XML entities."""
    if file.get('file_type') == 'xml':
        # Existing XML parser emits [tag] text. Count only those retained text
        # nodes; attributes, empty nodes and business-record counts are unknown.
        counts, examples = Counter(), {}
        for chunk in chunks:
            for match in re.finditer(r'(?m)^\[([^\]\n]{1,200})\] ([^\n]*)', chunk.text):
                tag, value = match.groups()
                counts[tag] += 1
                if tag in examples or len(examples) < 24:
                    examples.setdefault(tag, value[:180])
        return json.dumps(dict(kind='XML 已提取文本节点', text_node_counts=counts.most_common(60),
                               distinct_tags=len(counts), first_examples_by_tag=examples,
                               note='统计对象为索引保留的带标签文本节点，不是原始XML节点或业务记录总数；未分析属性和空节点，样本不保证覆盖异常。'), ensure_ascii=False)
    with reading_index.open_index(file) as index:
        if index is None:
            return '无结构索引，仅提供正文位置采样。'
        tables = {r[0] for r in index.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'objects' not in tables:
            return '无结构统计。'
        counts = Counter()
        names = []
        for row in index.execute('SELECT kind,name FROM objects ORDER BY id'):
            counts[row['kind']] += 1
            if len(names) < 60:
                names.append(str(row['name'])[:100])
        return json.dumps(dict(object_counts=dict(counts), first_object_names=names,
                               note='对象数量不是业务记录数量；名称列表仅列前60项'), ensure_ascii=False)
