"""Durable, cancellable per-document index maintenance jobs."""
import contextvars
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from src.core import accounts, permissions, vector_store
from src.core.ingest_state import ingestion_lock, publication_lock, TaskCancelledError
from src.db import metadata_db as db
from src.knowledge import reading_index
from src.qa import bm25_index

logger = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='document-index')
_stopping = threading.Event()


def get(task_id, kb_id):
    with db.get_cursor() as cur:
        row = cur.execute('SELECT * FROM index_tasks WHERE id=? AND kb_id=?',(task_id,kb_id)).fetchone()
    if not row:
        raise HTTPException(404,'索引任务不存在')
    result = dict(row)
    result['files'] = json.loads(result.pop('files_json'))
    result['results'] = json.loads(result.pop('results_json'))
    result['total'] = len(result['files'])
    return result


def update(task_id, **values):
    with db.get_cursor() as cur:
        cur.execute('UPDATE index_tasks SET '+','.join(k+'=?' for k in values)+',updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    [*values.values(),task_id])


def start(kb_id, file_ids=None, force=False):
    files = db.list_files_in_kb(kb_id)
    available = {f['id']:f for f in files}
    if file_ids and any(fid not in available for fid in file_ids):
        raise HTTPException(404,'部分文档不属于此知识库')
    selected = [f for f in files if f['status']=='done' and (not file_ids or f['id'] in file_ids)
                and (force or reading_index.info(f) is None)]
    if not selected:
        raise HTTPException(400,'没有需要补建的文档，可选择重建已有索引')
    task_id = uuid.uuid4().hex
    with db.get_cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        try:
            if cur.execute("SELECT 1 FROM index_tasks WHERE kb_id=? AND state IN ('queued','running','cancelling')",(kb_id,)).fetchone():
                raise HTTPException(409,'此知识库已有索引任务，请等待完成或取消')
            cur.execute('INSERT INTO index_tasks(id,kb_id,state,files_json) VALUES (?,?,?,?)',
                        (task_id,kb_id,'queued',json.dumps([f['id'] for f in selected])))
            cur.execute('COMMIT')
        except BaseException:
            cur.execute('ROLLBACK')
            raise
    context = contextvars.copy_context()
    try:
        _pool.submit(context.run, run, task_id, kb_id)
    except Exception:
        update(task_id,state='failed',detail='后台任务启动失败，请重试')
        raise
    return get(task_id,kb_id)


def run(task_id,kb_id):
    results = []
    def check():
        task = get(task_id,kb_id)
        if _stopping.is_set() or task['cancel_requested']:
            raise TaskCancelledError()
        if accounts.enabled:
            permissions.require_resource('kb',kb_id,'manage')
    try:
        check()
        update(task_id,state='running')
        task = get(task_id,kb_id)
        for fid in task['files']:
            check()
            builder = None
            published = False
            file = None
            try:
                with ingestion_lock:
                    check()
                    file = next((f for f in db.list_files_in_kb(kb_id) if f['id']==fid),None)
                    if not file or file['status']!='done':
                        raise ValueError('文档已删除或尚未入库')
                    def progress(detail):
                        check()
                        update(task_id,detail=f"{file['relative_path']} · {detail}")
                    progress('读取原文并建立索引')
                    builder = reading_index.build_file(file,check,progress)
                    # Repair keyword entries from the existing authoritative chunks;
                    # no embedding calls, no source-file changes.
                    kb = db.get_kb(kb_id)
                    ids = json.loads(file.get('chunk_ids_json') or '[]')
                    collection = vector_store.get_chroma_client().get_collection(kb['collection_name']) if ids else None
                    for offset in range(0,len(ids),100):
                        progress(f'更新关键词索引 {offset}/{len(ids)}')
                        rows = collection.get(ids=ids[offset:offset+100],include=['documents','metadatas'])
                        if set(rows['ids']) != set(ids[offset:offset+100]):
                            raise ValueError('原文检索片段缺失，请重新导入文档')
                        bm25_index.add_chunks([dict(id=cid,text=text,metadata=meta or {})
                            for cid,text,meta in zip(rows['ids'],rows['documents'],rows['metadatas'])])
                    with publication_lock, db.get_cursor() as cur:
                        check()
                        cur.execute('BEGIN IMMEDIATE')
                        try:
                            current = cur.execute('SELECT content_hash,status FROM knowledge_files WHERE id=? AND kb_id=?',(fid,kb_id)).fetchone()
                            cancelled = cur.execute('SELECT cancel_requested FROM index_tasks WHERE id=?',(task_id,)).fetchone()
                            if not cancelled or cancelled[0]:
                                raise TaskCancelledError()
                            if not current or current['content_hash']!=file['content_hash'] or current['status']!='done':
                                raise ValueError('文档版本已变化，未发布过期索引')
                            builder.publish(cur,fid,file['content_hash'])
                            cur.execute('COMMIT')
                            published = True
                        except BaseException:
                            cur.execute('ROLLBACK')
                            raise
                    results.append(dict(file_id=fid,name=file['relative_path'],status='done'))
                    logger.info('文档索引重建完成 kb=%s file=%s version=%s sections=%s objects=%s',kb_id,fid,file['content_hash'],builder.sections,builder.objects)
            except TaskCancelledError:
                raise
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception('文档索引重建失败 kb=%s file=%s',kb_id,fid)
                results.append(dict(file_id=fid,name=file['relative_path'] if file else str(fid),status='failed',error=str(exc)[:500]))
            finally:
                if builder and not published:
                    builder.discard()
            update(task_id,completed=len(results),failed=sum(r['status']=='failed' for r in results),results_json=json.dumps(results,ensure_ascii=False))
        update(task_id,state='partial' if any(r['status']=='failed' for r in results) else 'done',detail='索引任务完成')
    except TaskCancelledError:
        update(task_id,state='cancelled',detail='已安全取消，已完成的文档保留，未发布的索引已清理')
    except Exception as exc:
        logger.exception('索引任务停止 kb=%s task=%s',kb_id,task_id)
        update(task_id,state='failed',detail=getattr(exc,'detail',str(exc))[:500])


def recover():
    _stopping.clear()
    with db.get_cursor() as cur:
        cur.execute("UPDATE index_tasks SET state='failed',detail='服务中断，已发布索引保留；可重新补建或重建' WHERE state IN ('queued','running','cancelling')")
        active = {r[0] for r in cur.execute('SELECT artifact FROM document_read_indexes')}
        active.update(r[0] for r in cur.execute('SELECT reading_artifact FROM ingest_runs') if r[0])
    for path in reading_index.root().glob('*.sqlite'):
        if path.name not in active:
            try:
                path.unlink()
            except OSError:
                logger.warning('索引临时文件稍后清理: %s',path.name)


def shutdown():
    _stopping.set()
    _pool.shutdown(wait=False,cancel_futures=True)
