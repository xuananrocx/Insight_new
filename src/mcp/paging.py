"""Bound complete MCP envelopes; page lists without dropping their tail.

Pages keep a version-bound snapshot, never conversation/agent state.
"""
from __future__ import annotations

import copy
import json

from fastapi import HTTPException
from src.mcp import contracts
from src.qa import tool_navigation

LISTS = ('evidence', 'unassessed_candidates', 'documents', 'sections', 'objects', 'members', 'sheets', 'document_catalog')
NEXT = ('next_scan_cursor', 'next_cursor', 'next_offset', 'next_read_ref', 'previous_read_ref', 'next_section_ref', 'next_range', 'following_range')


def size(result):
    return len(json.dumps(result, ensure_ascii=False))


def projected(result, kb):
    """Real handle lengths and envelope; use a fixed placeholder for sizing only."""
    def convert(v):
        if isinstance(v, dict):
            return {k: convert(x) for k, x in v.items() if not k.startswith('_')}
        if isinstance(v, list):
            return [convert(x) for x in v]
        if isinstance(v, str) and (v in kb.read_refs or v in kb.scope_refs or v in kb.search_cursors or v in kb.result_refs or v in kb.scan_refs):
            return 'mcp_' + '0' * 40
        return v
    return convert(result)


def fit(kb, result, name, arguments, maximum):
    result = copy.deepcopy(result)
    result.pop('tool_metrics', None)
    result['diagnostics'] = {'phase_ms': {k: round(v, 3) for k, v in kb.timings.items()},
        'timing_scope': 'current_call_before_output_paging; phases_may_overlap; not_end_to_end',
        'request_log_key': contracts.request_id()}
    for ev in [*result.get('evidence', []), *result.get('unassessed_candidates', [])]:
        spreadsheet = (ev.get('structure') or {}).get('spreadsheet') or {}
        header = spreadsheet.get('display_header')
        if header and size(header) > maximum//8 and ev.get('reading', {}).get('artifact'):
            # The first row is a display hint, not a business header. A huge
            # duplicate of its cells must not prevent reading any later row.
            from src.qa.indexed_reading import reference
            ref = reference(kb, {'id': ev['document_id'], 'content_hash': ev['version']},
                ev['reading']['artifact'], ev['section'], header['start'], header['end'])
            spreadsheet['display_header'] = {k: header[k] for k in ('row', 'start', 'end')}
            spreadsheet['display_header'].update(values_omitted=True, read_ref=ref,
                note='Display hint exceeds output budget; read original through read_ref. Not a verified business header.')
    def envelope(value):
        decorated = contracts.decorate(value, name, arguments)
        decorated.update(kb_id=kb.kb_id, reference_expires_in_seconds=kb.reference_seconds)
        return decorated
    full = envelope(result)
    if size(projected(full, kb)) <= maximum:
        return full
    # Table rows refer to evidence citations and merge anchors. Keep those atomic;
    # the caller retries a smaller row range rather than splitting their relationship.
    if 'rows' in result:
        raise HTTPException(413, '表格行与引用元数据超过单次上限，请缩小 row_start/row_end 范围')
    lists = {key: result[key] for key in LISTS if isinstance(result.get(key), list) and result[key]}
    if not lists:
        raise HTTPException(413, '单条资料元数据超过返回上限，请缩小查询范围')
    tail = copy.deepcopy(result)
    page = {k: v for k, v in result.items() if k not in NEXT}
    for key in lists:
        page[key] = []
    page['output_paged'] = True
    page['coverage'] = {**(result.get('coverage') or {}), 'return_page_complete': False}
    # A result page does not inherit full-range claims from an unsplit read.
    for key in ('range_complete', 'object_complete', 'returned_to_end'):
        if key in page['coverage']:
            page['coverage'][key] = False
    if 'read_status' in page:
        page['read_status'] = {**page['read_status'], 'range_complete': False, 'page_truncated': True}
    handle = 'result_pending'
    kb.result_refs[handle] = None
    page['next_cursor'] = handle
    accepted = 0
    for key, values in lists.items():
        taken = 0
        for value in values:
            page[key].append(value)
            if size(projected(envelope(page), kb)) > maximum:
                page[key].pop()
                break
            taken += 1
            accepted += 1
        tail[key] = values[taken:]
    if not accepted:
        kb.result_refs.pop(handle, None)
        raise HTTPException(413, '单条证据超过返回上限，请用 read_ref 分段读取或缩小范围')
    # Store only immutable data and reference descriptors required to replay it.
    versions = []
    for file in kb.files():
        # Cover the queried documents, including empty catalogs and metadata-only pages.
        from src.knowledge import reading_index
        info = reading_index.info(file)
        versions.append({'id': file['id'], 'version': file['content_hash'],
                         'artifact': info['artifact'] if info else None, 'status': file['status']})
    kb.result_refs[handle] = {'result': tail, 'name': name, 'arguments': arguments,
        'read_refs': kb.read_refs, 'scope_refs': kb.scope_refs,
        'search_cursors': kb.search_cursors, 'search_pages': kb.search_pages, 'scan_refs': kb.scan_refs,
        'versions': versions}
    return envelope(page)


def restore(kb, snapshot):
    for saved in snapshot['versions']:
        file = kb.file(saved['id'])
        if file['status'] != saved.get('status', 'done') or file['content_hash'] != saved['version']:
            raise ValueError('文档已更新，返回分页已失效')
        if saved.get('artifact'):
            tool_navigation.validate_snapshot(kb, {'versions': [saved]})
    for attr in ('read_refs', 'scope_refs', 'search_cursors', 'search_pages', 'scan_refs'):
        setattr(kb, attr, snapshot.get(attr, {}))
    return snapshot['result'], snapshot['name'], snapshot['arguments']
