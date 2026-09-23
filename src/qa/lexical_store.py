"""Incremental FTS5 index; Chinese segmentation matches the legacy tokenizer."""
import json
import sqlite3
import threading
from contextlib import contextmanager

from src.db import metadata_db as db

_lock = threading.RLock()


def path():
    return db.settings.get_path('metadata_db').parent / 'keyword-index.sqlite'


@contextmanager
def connection():
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT)')
        conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(id UNINDEXED,kb UNINDEXED,payload UNINDEXED,words)')
        conn.execute('CREATE TABLE IF NOT EXISTS chunk_keys(id TEXT PRIMARY KEY,row_id INTEGER UNIQUE)')
        if not conn.execute("SELECT 1 FROM state WHERE key='keys_v1'").fetchone():
            conn.execute('INSERT OR REPLACE INTO chunk_keys SELECT id,rowid FROM chunks')
            conn.execute("INSERT INTO state VALUES ('keys_v1','1')")
        if not conn.execute("SELECT 1 FROM state WHERE key='migrated'").fetchone():
            legacy = target.parent / 'bm25_index.json'
            if legacy.exists():
                old = json.loads(legacy.read_text('utf-8'))
                from src.qa.bm25_index import _tokenize
                for chunk in old.get('chunks', []):
                    _insert(conn, chunk, _tokenize)
            conn.execute("INSERT INTO state VALUES ('migrated','1')")
        conn.commit()
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert(conn, chunk, tokenize):
    content = ' '.join(str(chunk.get(k, '')) for k in ('text','title','source_name','section_label'))
    _remove(conn,chunk['id'])
    row = conn.execute('INSERT INTO chunks(id,kb,payload,words) VALUES (?,?,?,?)',
                 (chunk['id'], chunk.get('kb_id') or 'default', json.dumps(chunk,ensure_ascii=False), ' '.join(tokenize(content))))
    conn.execute('INSERT INTO chunk_keys VALUES (?,?)',(chunk['id'],row.lastrowid))


def _remove(conn,cid):
    row=conn.execute('SELECT row_id FROM chunk_keys WHERE id=?',(cid,)).fetchone()
    if row:
        conn.execute('DELETE FROM chunks WHERE rowid=?',(row[0],))
        conn.execute('DELETE FROM chunk_keys WHERE id=?',(cid,))


def add(chunks):
    from src.qa.bm25_index import _tokenize
    with _lock, connection() as conn:
        for chunk in chunks:
            _insert(conn, {'id':chunk['id'],'text':chunk['text'],**(chunk.get('metadata') or {})}, _tokenize)


def remove(ids):
    with _lock, connection() as conn:
        for cid in ids:
            _remove(conn,cid)


def clear():
    with _lock, connection() as conn:
        conn.execute('DELETE FROM chunks')
        conn.execute('DELETE FROM chunk_keys')


def query(text, limit=20, kb_id=None, enhanced=False):
    from src.qa.bm25_index import _tokenize, identifiers
    import re
    words = list(dict.fromkeys(_tokenize(text)))[:64]
    if not words:
        return []
    expression = ' OR '.join('"'+word.replace('"','""')+'"' for word in words)
    blocked = db.unpublished_chunk_ids()
    with _lock, connection() as conn:
        rows = conn.execute('SELECT payload,-bm25(chunks) AS score FROM chunks WHERE chunks MATCH ?'
                            + (' AND kb=?' if kb_id is not None else '') + ' ORDER BY bm25(chunks)',
                            [expression]+([kb_id] if kb_id is not None else []))
        hits = []
        for row in rows:
            hit = json.loads(row['payload'])
            if hit['id'] in blocked or hit.get('chunk_type') == 'summary':
                continue
            hit['bm25_score'] = row['score']
            hits.append(hit)
            if len(hits) >= max(limit, 200 if enhanced else limit):
                break
    if enhanced:
        fields = identifiers(text)
        for hit in hits:
            hit['exact_matches'] = sum(bool(re.search(r'(?<![a-z0-9_])'+re.escape(f)+r'(?![a-z0-9_])',hit['text'],re.I)) for f in fields)
        hits.sort(key=lambda h:(h['exact_matches'],h['bm25_score']),reverse=True)
    return hits[:limit]


def stats():
    with _lock, connection() as conn:
        count = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    return {'chunk_count':count,'persisted':True,'index_file':str(path()),'backend':'sqlite_fts5'}
