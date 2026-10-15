"""Recoverable vector/SQLite publication, sharing the ingestion publication lock."""
import json
import logging
from src.core import vector_store
from src.core.ingest_state import publication_lock
from src.db import metadata_db as db

logger = logging.getLogger(__name__)


def init_schema():
    with db.get_cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS summary_publications (
            chunk_id TEXT PRIMARY KEY, collection_name TEXT NOT NULL,
            previous_json TEXT, state TEXT NOT NULL DEFAULT 'pending')''')


def restore(chunk_id, collection_name, previous):
    if previous:
        vector_store.upsert_chunks(chunks=[previous], collection_name=collection_name)
    else:
        vector_store.delete_chunks([chunk_id], collection_name=collection_name)


def recover():
    init_schema()
    with publication_lock, db.get_cursor() as cur:
        rows = cur.execute('SELECT * FROM summary_publications').fetchall()
        for row in rows:
            if row['state'] == 'pending':
                restore(row['chunk_id'], row['collection_name'], json.loads(row['previous_json']) if row['previous_json'] else None)
            cur.execute('DELETE FROM summary_publications WHERE chunk_id=?', (row['chunk_id'],))


def publish(file_id, kb_id, content_hash, chunk, collection_name, model, tokens, concepts, check):
    from src.knowledge import summary_tasks
    summary_tasks.init_schema()
    init_schema()
    with publication_lock:
        check()
        previous = None
        with db.get_cursor() as cur:
            old = cur.execute('SELECT summary FROM document_meta WHERE file_id=?', (file_id,)).fetchone()
        if old and old['summary']:
            collection = vector_store.get_or_create_collection(collection_name)
            data = collection.get(ids=[chunk['id']], include=['documents', 'metadatas', 'embeddings'])
            if data['ids']:
                previous = dict(id=chunk['id'], text=data['documents'][0],
                                metadata=data['metadatas'][0], embedding=list(map(float, data['embeddings'][0])))
        with db.get_cursor() as cur:
            cur.execute('INSERT OR REPLACE INTO summary_publications VALUES (?,?,?,?)',
                        (chunk['id'], collection_name, json.dumps(previous) if previous else None, 'pending'))
        try:
            check()
            with db.get_cursor() as cur:
                cur.execute('BEGIN IMMEDIATE')
                try:
                    file = cur.execute('SELECT content_hash FROM knowledge_files WHERE id=? AND kb_id=?', (file_id, kb_id)).fetchone()
                    if not file or file['content_hash'] != content_hash:
                        raise ValueError('文档版本已变化，未发布摘要')
                    db.upsert_document_meta(file_id=file_id, kb_id=kb_id, processed_level='summarized',
                        summary=chunk['text'], summary_model=model, summary_tokens=tokens,
                        concepts_extracted=len(concepts), concepts_count=len(concepts), _cursor=cur)
                    db._remove_file_from_concepts_in_tx(cur, file_id)
                    for concept in concepts:
                        db.upsert_kb_concept(kb_id=kb_id, concept_name=concept['name'],
                            concept_type=concept['type'], description=concept['description'], source_file_id=file_id, _cursor=cur)
                    cur.execute('INSERT OR REPLACE INTO summary_versions VALUES (?,?,?)',
                                (file_id, content_hash, summary_tasks.digest(chunk['text'])))
                    vector_store.upsert_chunks(chunks=[chunk], collection_name=collection_name)
                    cur.execute("UPDATE summary_publications SET state='committed' WHERE chunk_id=?", (chunk['id'],))
                    cur.execute('COMMIT')
                except BaseException:
                    cur.execute('ROLLBACK')
                    raise
        except BaseException:
            try:
                restore(chunk['id'], collection_name, previous)
                with db.get_cursor() as cur:
                    cur.execute('DELETE FROM summary_publications WHERE chunk_id=?', (chunk['id'],))
            except Exception:
                logger.exception('摘要向量回滚待启动恢复 file=%s', file_id)
            raise
        with db.get_cursor() as cur:
            cur.execute('DELETE FROM summary_publications WHERE chunk_id=?', (chunk['id'],))
