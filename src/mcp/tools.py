"""Thin adapters over the unchanged internal knowledge tools; no chat state."""
from __future__ import annotations

import copy
import json
import secrets
import time

from fastapi import HTTPException
from src.core import accounts as a
from src.db import metadata_db as db
from src.qa.knowledge_tools import KnowledgeTools, TOOLS
from src.mcp import store


def definitions():
    items = [{'name': 'list_knowledge_bases', 'description': '列出当前令牌及用户均有权查询的知识库。',
              'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    for tool in TOOLS:
        schema = copy.deepcopy(tool['parameters'])
        schema['properties']['kb_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 100, 'description': 'list_knowledge_bases 返回的知识库编号'}
        schema['required'].append('kb_id')
        if 'limit' in schema['properties']:
            schema['properties']['limit']['maximum'] = 20
            schema['properties']['limit']['description'] = '单次返回条数，1–20；使用 next_cursor 续页。'
        description = tool['description'].replace('本轮', '查询').replace('当前授权知识库', '指定授权知识库')
        description += ' 每次调用独立，不保存对话；已读范围仅描述本次调用。引用有有效期，失效后重新搜索或获取目录。'
        items.append({'name': tool['name'], 'description': description, 'inputSchema': schema})
    return items


class AccessTools(KnowledgeTools):
    def __init__(self, kb_id, token, check_cancel, max_chars):
        super().__init__(kb_id, check_cancel, max_chars=max_chars, top_k=8)
        self.token = token

    def check(self):
        self.check_cancel()
        store.require_access(self.token, self.kb_id)


def restore_reference(kb, argument, kind):
    rows = a.rows('SELECT * FROM mcp_references WHERE id=? AND token_id=? AND kb_id=? AND kind=? AND expires_at>?',
                  (argument, kb.token['id'], kb.kb_id, kind, int(time.time())))
    if not rows:
        raise HTTPException(400, '查询引用无效、已过期或不属于当前令牌和知识库，请重新搜索或获取目录')
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
    now = int(time.time())
    expiry = now + store.config().reference_minutes * 60
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
        if value in kb.read_refs:
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
            cur.execute('SELECT id,length(CAST(payload AS BLOB)) n FROM mcp_references WHERE token_id=? ORDER BY expires_at,id LIMIT 1', (kb.token['id'],))
            old = cur.fetchone()
            cur.execute('DELETE FROM mcp_references WHERE id=?', (old['id'],))
            size -= old['n']
            count -= 1
        cur.executemany('INSERT INTO mcp_references VALUES (?,?,?,?,?,?)', rows)
        while True:
            cur.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM mcp_references')
            if cur.fetchone()[0] <= 64_000_000:
                break
            cur.execute('DELETE FROM mcp_references WHERE id IN (SELECT id FROM mcp_references ORDER BY expires_at,id LIMIT 50)')
        cur.connection.commit()
    return converted


def execute(token, name, arguments, check_cancel):
    store.require_access(token)
    if name == 'list_knowledge_bases':
        if arguments:
            raise HTTPException(422, '该工具不接受参数')
        return {'knowledge_bases': store.allowed_kbs(token)}
    spec = next((s for s in definitions() if s['name'] == name), None)
    if not spec:
        raise HTTPException(400, '未知 MCP 工具')
    schema = spec['inputSchema']
    if set(arguments) - schema['properties'].keys() or set(schema['required']) - arguments.keys():
        raise HTTPException(422, '参数缺失或含有未授权字段，请按工具定义调用')
    args = dict(arguments)
    kid = args.pop('kb_id', None)
    if not isinstance(kid, str) or not 1 <= len(kid) <= 100:
        raise HTTPException(422, '请指定知识库编号 kb_id')
    store.require_access(token, kid)
    if 'limit' in args and (type(args['limit']) is not int or not 1 <= args['limit'] <= 20):
        raise HTTPException(422, '单次返回条数须为 1–20')
    kb = AccessTools(kid, token, check_cancel, store.config().max_output_chars // 2)
    kb.validate(name, args)  # Validate before resolving opaque references.
    for key, kind in [('read_ref', 'read'), ('scope_ref', 'scope'), ('cursor', 'cursor')]:
        if key in args:
            args[key] = restore_reference(kb, args[key], kind)
    result = kb.execute(name, args)
    kb.check()
    # Check the full serialized envelope, including catalog/outline metadata.
    if len(json.dumps(result, ensure_ascii=False)) > store.config().max_output_chars:
        raise HTTPException(413, '单次查询结果过大，请缩小范围或分页读取')
    if result.get('error') == 'evidence_budget':
        result['message'] = '本次请求已达单次返回上限，请缩小范围或分段读取'
    result = publish_references(kb, result)
    return result
