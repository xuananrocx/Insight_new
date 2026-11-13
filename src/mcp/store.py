"""Independent MCP configuration, personal tokens and bounded query references."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict

from src.core import accounts as a, permissions as p
from src.db import metadata_db as db


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    public_url: str = Field(default='', max_length=500)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    max_concurrent: int = Field(default=1, ge=1, le=4)
    max_output_chars: int = Field(default=32000, ge=16000, le=64000)
    requests_per_minute: int = Field(default=30, ge=1, le=120)
    reference_minutes: int = Field(default=30, ge=5, le=120)
    rerank_enabled: bool = True
    rerank_candidates: int = Field(default=24, ge=4, le=40)
    rerank_seconds: int = Field(default=12, ge=2, le=30)
    rerank_min_score: float = Field(default=0.10, ge=0, le=1)


def init_schema():
    with db.get_cursor() as cur:
        cur.executescript('''
        CREATE TABLE IF NOT EXISTS mcp_tokens (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
          token_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL, kb_ids TEXT NOT NULL,
          created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
          revoked_at INTEGER, last_used_at INTEGER);
        CREATE INDEX IF NOT EXISTS mcp_tokens_user ON mcp_tokens(user_id);
        CREATE TABLE IF NOT EXISTS mcp_references (
          id TEXT PRIMARY KEY, token_id TEXT NOT NULL, kb_id TEXT NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS mcp_refs_expiry ON mcp_references(expires_at);
        CREATE TABLE IF NOT EXISTS mcp_calls (
          id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, token_id TEXT NOT NULL,
          tool TEXT NOT NULL, kb_id TEXT, status TEXT NOT NULL,
          duration_ms INTEGER NOT NULL, created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS mcp_calls_time ON mcp_calls(created_at);
        ''')


RETRIEVAL_KEYS = {'rerank_enabled', 'rerank_candidates', 'rerank_seconds', 'rerank_min_score'}


def config() -> Settings:
    return Settings.model_validate({**db.get_state('mcp_settings', {}), **db.get_state('mcp_retrieval_settings', {})})


def save_config(value: Settings):
    from urllib.parse import urlsplit
    try:
        url = urlsplit(value.public_url)
        url.port  # Validate malformed ports and bracketed hosts before persistence.
    except ValueError:
        raise HTTPException(422, 'MCP 地址格式不正确')
    if value.public_url and (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in value.public_url)):
        raise HTTPException(422, '请输入不含凭据、查询参数的 MCP HTTP(S) 地址')
    if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise HTTPException(422, '远程 MCP 地址请使用 HTTPS；内网可使用 HTTPS 或安全隧道')
    # Old clients omit new fields; preserve the independent retrieval preferences.
    current = config()
    retrieval = {key: getattr(value if key in value.model_fields_set else current, key) for key in RETRIEVAL_KEYS}
    db.set_state('mcp_settings', value.model_dump(exclude=RETRIEVAL_KEYS))
    db.set_state('mcp_retrieval_settings', retrieval)
    a.audit('mcp_settings_changed')
    return config()


def token_view(row):
    return {k: row[k] for k in ('id', 'user_id', 'name', 'prefix', 'created_at', 'expires_at', 'revoked_at', 'last_used_at')} | {'kb_ids': json.loads(row['kb_ids'])}


def create_token(name: str, kb_ids: list[str], days: int):
    user = p.require('mcp.use')
    if not config().enabled:
        raise HTTPException(409, 'MCP 接入尚未开启')
    ids = sorted(set(kb_ids))
    if not ids or len(ids) > 100:
        raise HTTPException(422, '请选择 1–100 个知识库')
    for kid in ids:
        a.require_kb(kid)
    secret = 'insight_mcp_' + secrets.token_urlsafe(36)
    tid, now = uuid.uuid4().hex, int(time.time())
    with db.get_cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        cur.execute('SELECT count(*) FROM mcp_tokens WHERE user_id=? AND revoked_at IS NULL AND expires_at>?', (user['id'], now))
        if cur.fetchone()[0] >= 20:
            raise HTTPException(409, '最多保留 20 个有效令牌，请先撤销旧令牌')
        cur.execute('INSERT INTO mcp_tokens(id,user_id,name,token_hash,prefix,kb_ids,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                    (tid, user['id'], name.strip(), hashlib.sha256(secret.encode()).hexdigest(), secret[:19], json.dumps(ids), now, now + days * 86400))
        cur.connection.commit()
    a.audit('mcp_token_created', tid)
    row = a.rows('SELECT * FROM mcp_tokens WHERE id=?', (tid,))[0]
    return {'token': secret, 'item': token_view(row)}


def revoke(tid: str, *, admin=False):
    row = a.rows('SELECT * FROM mcp_tokens WHERE id=?', (tid,))
    if not row or (not admin and row[0]['user_id'] != a.user()['id']):
        raise HTTPException(404, '令牌不存在')
    with db.get_cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        cur.execute('UPDATE mcp_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (int(time.time()), tid))
        cur.execute('DELETE FROM mcp_references WHERE token_id=?', (tid,))
        cur.connection.commit()
    a.audit('mcp_token_revoked', tid)


def authenticate(secret: str):
    if not config().enabled:
        raise HTTPException(503, 'MCP 接入已关闭')
    if not secret or len(secret) > 200:
        raise HTTPException(401, 'MCP 令牌无效或已失效')
    now = int(time.time())
    found = a.rows('''SELECT t.* FROM mcp_tokens t JOIN auth_users u ON u.id=t.user_id
        WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>?
        AND u.disabled=0 AND u.must_change_password=0''', (hashlib.sha256(secret.encode()).hexdigest(), now))
    if not found:
        raise HTTPException(401, 'MCP 令牌无效或已失效')
    row = found[0]
    if not p.has('mcp.use', row['user_id']):
        raise HTTPException(403, '当前账号没有外部 Agent 接入权限')
    return row


def require_access(token, kb_id=None):
    # Recheck the token and current user/capability even on an existing connection.
    if not config().enabled:
        raise HTTPException(503, 'MCP 接入已关闭')
    fresh = a.rows('SELECT * FROM mcp_tokens WHERE id=? AND revoked_at IS NULL AND expires_at>?', (token['id'], int(time.time())))
    if not fresh or a.user()['id'] != token['user_id']:
        raise HTTPException(401, 'MCP 令牌已失效')
    if a.user()['must_change_password']:
        raise HTTPException(403, '请先完成密码修改')
    p.require('mcp.use')
    if kb_id is not None:
        if kb_id not in json.loads(fresh[0]['kb_ids']):
            raise HTTPException(403, '知识库不在令牌授权范围内')
        a.require_kb(kb_id)


def allowed_kbs(token):
    require_access(token)
    ids = set(json.loads(token['kb_ids'])) & set(a.visible_kb_ids())
    result = []
    for kid in sorted(ids):
        item = db.get_kb(kid)
        if item:
            result.append({'kb_id': kid, 'name': item['name'], 'description': item.get('description', '')})
    return result


def audit_call(token, tool, kb_id, status, started):
    now = int(time.time())
    with db.get_cursor() as cur:
        cur.execute('INSERT INTO mcp_calls(user_id,token_id,tool,kb_id,status,duration_ms,created_at) VALUES (?,?,?,?,?,?,?)',
                    (token['user_id'], token['id'], tool, kb_id, status, int((time.monotonic()-started)*1000), now))
        cur.execute('DELETE FROM mcp_calls WHERE created_at<?', (now-30*86400,))
        cur.execute('DELETE FROM mcp_calls WHERE id IN (SELECT id FROM mcp_calls ORDER BY id DESC LIMIT -1 OFFSET 10000)')
        cur.execute('UPDATE mcp_tokens SET last_used_at=? WHERE id=?', (now, token['id']))
