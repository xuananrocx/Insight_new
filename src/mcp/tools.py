"""Thin adapters over the unchanged internal knowledge tools; no chat state."""
from __future__ import annotations

import copy
import json
import secrets
import time
import logging

from fastapi import HTTPException
from src.core import accounts as a
from src.db import metadata_db as db
from src.qa.knowledge_tools import KnowledgeTools, TOOLS
from src.mcp import store, contracts, paging, search, ranked_search

log = logging.getLogger(__name__)


def definitions():
    items = [{'name': 'list_knowledge_bases', 'description': contracts.DESCRIPTIONS['list_knowledge_bases'],
              'inputSchema': {'type': 'object', 'properties': {
                  'query': {'type': 'string', 'maxLength': 200},
                  'offset': {'type': 'integer', 'minimum': 0},
                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, 'additionalProperties': False}}]
    for tool in TOOLS:
        schema = copy.deepcopy(tool['parameters'])
        schema['properties']['kb_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 100, 'description': 'list_knowledge_bases 返回的知识库编号'}
        schema['required'].append('kb_id')
        if 'limit' in schema['properties']:
            schema['properties']['limit'].update(maximum=20, description='返回上限 1–20；next_cursor 继续固定结果。')
        for key, value in schema['properties'].items():
            if key in ('read_ref', 'scope_ref', 'cursor'):
                value['description'] = {'read_ref': '目录/搜索返回的原文入口，不是 scope_ref',
                    'scope_ref': '目录返回的范围入口，不是 read_ref', 'cursor': '返回的 next_cursor，不自行拼接'}[key]
        if tool['name'] in ('search_knowledge', 'search_document'):
            schema['properties']['query_variants'] = {'type': 'array', 'maxItems': 2, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                'description': 'Agent 提供的可选同义/原文语言查询，最多2项；仅 related/keyword/semantic/hybrid，和原查询共享本次限额。不自动翻译。'}
        if tool['name'] == 'continue_search':
            schema['required'].remove('cursor')
            schema['properties']['scan_cursor'] = {'type': 'string', 'minLength': 1, 'maxLength': 80, 'description': 'next_scan_cursor：继续尚未扫描的原文；与 cursor 二选一'}
            schema['oneOf'] = [{'required': ['cursor']}, {'required': ['scan_cursor']}]
        if tool['name'] == 'get_kb_overview':
            schema['properties']['query'] = {'type': 'string', 'maxLength': 200, 'description': '可选文件名关键词；不提供时为目录样本'}
        items.append({'name': tool['name'], 'description': contracts.DESCRIPTIONS[tool['name']], 'inputSchema': schema})
    for item in items:
        item['outputSchema'] = copy.deepcopy(contracts.OUTPUT_SCHEMA)
    return items


class AccessTools(KnowledgeTools):
    def __init__(self, kb_id, token, check_cancel, max_chars):
        super().__init__(kb_id, check_cancel, max_chars=max_chars, top_k=8)
        self.token = token
        self.result_refs = {}
        self.scan_refs = {}
        self.reference_seconds = store.config().reference_minutes * 60
        self._access_checked = 0.0
        self.access_ms = 0.0
        self.files_ms = 0.0

    def files(self):
        started = time.monotonic()
        try:
            return super().files()
        finally:
            self.files_ms += (time.monotonic()-started)*1000

    @staticmethod
    def brief(file):
        from src.knowledge import reading_index
        return {**KnowledgeTools.brief(file), 'capabilities': {
            'canonical_reading': reading_index.info(file) is not None,
            'indexed_chunks_listed': bool(KnowledgeTools.ids(file)),
            'vector_health': 'not_probed'}}

    def check(self):
        self.check_cancel()
        now = time.monotonic()
        if now-self._access_checked >= .1:
            store.require_access(self.token, self.kb_id)
            self._access_checked = now
            self.access_ms += (time.monotonic()-now)*1000


def restore_reference(kb, argument, kind):
    rows = a.rows('SELECT * FROM mcp_references WHERE id=? AND token_id=? AND kb_id=? AND kind=? AND expires_at>?',
                  (argument, kb.token['id'], kb.kb_id, kind, int(time.time())))
    if not rows:
        raise HTTPException(400, contracts.error('reference_invalid', '查询引用无效、已过期或不属于当前令牌和知识库，请重新搜索或获取目录', legacy='400'))
    payload = json.loads(rows[0]['payload'])
    internal = payload['ref']
    if kind == 'read':
        kb.read_refs[internal] = payload['spec']
    elif kind == 'scope':
        kb.scope_refs[internal] = payload['spec']
    else:
        kb.search_cursors[internal] = payload['spec']
        kb.search_pages[payload['spec']['key']] = payload['snapshot']
    return internal


def publish_references(kb, result):
    publication_started = time.monotonic()
    now = int(time.time())
    expiry = now + kb.reference_seconds
    rows, mapped = [], {}

    def convert(value):
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items() if not k.startswith('_')}
        if not isinstance(value, str):
            return value
        if value in mapped:
            return mapped[value]
        if value in kb.scan_refs:
            kind, payload = 'scan', kb.scan_refs[value]
        elif value in kb.result_refs:
            kind, payload = 'result', kb.result_refs[value]
        elif value in kb.read_refs:
            kind, payload = 'read', {'ref': value, 'spec': kb.read_refs[value]}
        elif value in kb.scope_refs:
            kind, payload = 'scope', {'ref': value, 'spec': kb.scope_refs[value]}
        elif value in kb.search_cursors:
            kind, payload = 'cursor', {'ref': value, 'spec': kb.search_cursors[value], 'snapshot': kb.search_pages[kb.search_cursors[value]['key']]}
        else:
            return value
        handle = 'mcp_' + secrets.token_hex(20)
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 8_000_000:
            raise HTTPException(413, '查询分页快照过大，请缩小搜索范围')
        mapped[value] = handle
        rows.append((handle, kb.token['id'], kb.kb_id, kind, encoded, expiry))
        return handle

    converted = convert(result)
    converted['kb_id'] = kb.kb_id
    converted['reference_expires_in_seconds'] = expiry - now
    if len(json.dumps(converted, ensure_ascii=False)) > store.config().max_output_chars:
        raise HTTPException(413, '单次查询结果过大，请缩小范围或分页读取')
    kb.check()
    store.require_access(kb.token, kb.kb_id)
    with db.get_cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        cur.execute('DELETE FROM mcp_references WHERE expires_at<=?', (now,))
        # Keep a maximum of 32 MB and 1000 handles per token, 64 MB globally.
        cur.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0),count(*) FROM mcp_references WHERE token_id=?', (kb.token['id'],))
        size, count = cur.fetchone()
        added = sum(len(row[4].encode()) for row in rows)
        if added > 32_000_000 or len(rows) > 1000:
            raise HTTPException(413, '查询引用过多，请缩小查询范围')
        while size + added > 32_000_000 or count + len(rows) > 1000:
            cur.execute('SELECT id,length(CAST(payload AS BLOB)) n FROM mcp_references WHERE token_id=? ORDER BY rowid LIMIT 1', (kb.token['id'],))
            old = cur.fetchone()
            cur.execute('DELETE FROM mcp_references WHERE id=?', (old['id'],))
            size -= old['n']
            count -= 1
        cur.executemany('INSERT INTO mcp_references VALUES (?,?,?,?,?,?)', rows)
        while True:
            cur.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM mcp_references')
            if cur.fetchone()[0] <= 64_000_000:
                break
            cur.execute('DELETE FROM mcp_references WHERE id IN (SELECT id FROM mcp_references ORDER BY rowid LIMIT 1)')
        cur.connection.commit()
    log.info('MCP publication request=%s publication_ms=%.3f phase_ms=%s access_ms=%.3f files_inclusive_ms=%.3f output_chars=%d',
             contracts.request_id(), (time.monotonic()-publication_started)*1000,
             json.dumps(kb.timings), kb.access_ms, kb.files_ms, paging.size(converted))
    return converted


def _execute(token, name, arguments, check_cancel):
    store.require_access(token)
    spec = next((s for s in definitions() if s['name'] == name), None)
    if not spec:
        raise HTTPException(400, '未知 MCP 工具')
    schema = spec['inputSchema']
    if not isinstance(arguments, dict) or set(arguments) - schema['properties'].keys() or set(schema.get('required', [])) - arguments.keys():
        raise HTTPException(422, '参数缺失或含有未授权字段，请按工具定义调用')
    for key, value in arguments.items():
        prop = schema['properties'][key]
        if prop['type'] == 'array':
            valid = isinstance(value, list) and len(value) <= 2 and all(isinstance(v, str) and 1 <= len(v.strip()) <= 200 for v in value)
        elif prop['type'] == 'integer':
            valid = type(value) is int and prop.get('minimum', 0) <= value <= prop.get('maximum', 10000000)
        else:
            valid = isinstance(value, str) and prop.get('minLength', 0) <= len(value.strip()) <= prop.get('maxLength', 1000)
        if not valid or ('enum' in prop and value not in prop['enum']):
            raise HTTPException(422, f'{key} 不符合工具参数定义')
    if name == 'list_knowledge_bases':
        query = arguments.get('query', '').casefold()
        rows = sorted((v for v in store.allowed_kbs(token) if query in (v['name']+' '+(v.get('description') or '')).casefold()), key=lambda v: v['kb_id'])
        offset, limit = arguments.get('offset', 0), arguments.get('limit', 20)
        page = []
        for row in rows[offset:offset+limit]:
            row = {**row, 'description': (row.get('description') or '')[:2000],
                   'description_truncated': len(row.get('description') or '') > 2000}
            candidate = contracts.decorate({'knowledge_bases': page+[row], 'total': len(rows), 'next_offset': offset+len(page)+1}, name, arguments)
            if paging.size(candidate) > store.config().max_output_chars:
                break
            page.append(row)
        if not page and offset < len(rows):
            raise HTTPException(413, '知识库描述过长，请使用更具体的 query 查找其他知识库')
        return contracts.decorate({'knowledge_bases': page, 'total': len(rows),
            'next_offset': offset+len(page) if offset+len(page)<len(rows) else None}, name, arguments)
    args = dict(arguments)
    kid = args.pop('kb_id')
    store.require_access(token, kid)
    maximum = store.config().max_output_chars
    kb = AccessTools(kid, token, check_cancel, max(1000, maximum-6000))
    if name == 'continue_search' and (('cursor' in args) == ('scan_cursor' in args)):
        raise HTTPException(422, 'cursor 与 scan_cursor 必须且只能提供一个')
    if name == 'continue_search' and 'scan_cursor' in args:
        rows = a.rows('SELECT payload FROM mcp_references WHERE id=? AND token_id=? AND kb_id=? AND kind=? AND expires_at>?',
            (args['scan_cursor'], token['id'], kid, 'scan', int(time.time())))
        if not rows:
            raise HTTPException(400, contracts.error('reference_invalid', '扫描引用已失效，请重新搜索', legacy='400'))
        state = json.loads(rows[0]['payload'])
        try:
            result = search.scan(kb, state, state.get('limit', 8))
        except ValueError:
            return contracts.error('document_changed', '文档或阅读索引已变化，请重新搜索')
        return publish_references(kb, paging.fit(kb, result, name, arguments, maximum))
    if name == 'continue_search':
        rows = a.rows('SELECT payload FROM mcp_references WHERE id=? AND token_id=? AND kb_id=? AND kind=? AND expires_at>?',
            (args['cursor'], token['id'], kid, 'result', int(time.time())))
        if rows:
            try:
                result, original_name, original_args = paging.restore(kb, json.loads(rows[0]['payload']))
            except ValueError:
                return contracts.error('document_changed', '文档或索引已变化，请重新查询')
            return publish_references(kb, paging.fit(kb, result, original_name, original_args, maximum))
    variants = args.pop('query_variants', [])
    if variants and args.get('match_mode') in ('exact', 'identifier'):
        raise HTTPException(422, 'exact/identifier 不接受 query_variants，请分别执行字面查询')
    query = args.pop('query', '') if name == 'get_kb_overview' else None
    if query is not None:
        kb.question = query
    kb.validate(name, args)
    for key, kind in [('read_ref', 'read'), ('scope_ref', 'scope'), ('cursor', 'cursor')]:
        if key in args:
            args[key] = restore_reference(kb, args[key], kind)
    result = None
    if name in ('search_knowledge', 'search_document', 'search_scope') and args.get('match_mode', 'related') in ('exact', 'identifier'):
        bounds = {}
        if name == 'search_scope':
            from src.qa.tool_navigation import resolve
            try:
                file, spec = resolve(kb, args['scope_ref'])
            except ValueError:
                return contracts.error('document_changed', '范围已失效，请重新获取目录')
            files = [file]
            bounds = {k: v for k, v in spec.items() if k in ('section', 'object_id', 'sheet')}
        else:
            files = [kb.file(args['document_id'])] if 'document_id' in args else [f for f in kb.files() if f['status'] == 'done']
        state = search.begin(kb, files, args['query'], args['match_mode'], bounds)
        if state is not None:
            state['limit'] = args.get('limit', 8)
            result = search.scan(kb, state, state['limit'], args.get('offset', 0))
    elif name in ('search_knowledge', 'search_document'):
        result = ranked_search.search(kb, args, variants)
    elif name == 'continue_search':
        from src.qa.tool_navigation import get_page
        try:
            spec, snap = get_page(kb, args['cursor'])
        except ValueError:
            return contracts.error('document_changed', '文档或索引已变化，请重新搜索')
        if snap['kind'] == 'mcp_canonical':
            result = search.page(kb, spec['key'], snap, spec['offset'], spec['limit'])
        elif snap['kind'] == 'mcp_ranked':
            result = ranked_search.page(kb, spec['key'], snap, spec['offset'], spec['limit'])
    if result is None:
        result = kb.execute(name, args)
    if name == 'get_kb_overview':
        result['catalog_scope'] = 'query_filename_candidates' if query else 'sample_catalog'
        result['catalog_query'] = query or None
    kb.check()
    if result.get('error') == 'evidence_budget':
        result['message'] = '本次返回达到上限；已有证据仍有效，请使用继续入口'
    for attempt in range(8):
        try:
            return publish_references(kb, paging.fit(kb, result, name, arguments, maximum))
        except HTTPException as exc:
            if exc.status_code != 413 or name not in ('read_document', 'read_table_rows'):
                raise
            kb.check()
            kb.hits.clear()
            kb.cache.clear()
            kb.used_chars = 0
            kb.result_refs.clear()
            if name == 'read_table_rows':
                start, stop = args['row_start'], args['row_end']
                if stop <= start:
                    # A giant row is still readable via its canonical read reference.
                    pending = result.get('pending_row') or next(iter(result.get('rows', [])), {})
                    ref = pending.get('read_ref')
                    if ref:
                        result = {'status': 'partial', 'rows': [], 'evidence': [], 'pending_row': {'row': start, 'read_ref': ref, 'reason': 'output_limit'},
                            'requested_range': {'start': start, 'end': arguments['row_end']}, 'range_complete': False,
                            'next_range': {'start': start+1, 'end': arguments['row_end']} if start < arguments['row_end'] else None}
                        return publish_references(kb, paging.fit(kb, result, name, arguments, maximum))
                    raise
                args['row_end'] = start + (stop-start)//2
            else:
                kb.max_chars = max(128, kb.max_chars//2)
            result = kb.execute(name, args)
            if name == 'read_table_rows' and not result.get('error'):
                original_end = arguments['row_end']
                result['requested_range']['end'] = original_end
                end = (result.get('returned_range') or {}).get('end', args['row_start']-1)
                result['range_complete'] = end >= original_end
                result['next_range'] = {'start': end+1, 'end': original_end} if end < original_end else None
    raise HTTPException(413, '单条资料元数据超限，请缩小范围')


def execute(token, name, arguments, check_cancel):
    started = time.monotonic()
    try:
        return _execute(token, name, arguments, check_cancel)
    finally:
        log.info('MCP tool request=%s tool=%s total_ms=%.3f', contracts.request_id(), name, (time.monotonic()-started)*1000)
