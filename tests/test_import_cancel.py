from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from src.core import accounts, vector_store
from src.core.ingest_state import current_upload_task, CleanupError
from src.db import metadata_db as db
from src.knowledge import ingestion, batch_upload as worker
from src.knowledge.ingest_transaction import IngestTransaction, pending_runs, cleanup_run
from src.knowledge.chunker import Chunk
from src.qa import bm25_index


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db.init_db()
    monkeypatch.setattr(accounts, "enabled", False)
    path = tmp_path/'replace.md';path.write_text('new content')
    tid=worker.generate_task_id()
    db.create_upload_task(task_id=tid,kb_id='default',skip_mode='overwrite',auto_ingest=True,
        files=[{'relative_path':path.name,'absolute_path':str(path),'file_size':11}])
    fid=db.upsert_file(relative_path=str(path),absolute_path=str(path),content_hash='old-hash',
        file_size=3,file_type='md',kb_id='default')
    db.set_file_processed(fid,['old-chunk'])
    stored={'old-chunk':{'id':'old-chunk','text':'old content','metadata':{'file_id':fid},'embedding':[1.0,0.0]}}
    lexical=dict(stored)
    def upsert(chunks, **kwargs):
        for c in chunks: stored[c['id']]=dict(c)
    def delete(ids):
        for cid in ids: stored.pop(cid,None)
    def add(chunks):
        for c in chunks: lexical[c['id']]=c
    def remove(ids):
        for cid in ids: lexical.pop(cid,None)
    monkeypatch.setattr(vector_store,'upsert_chunks',upsert)
    monkeypatch.setattr(vector_store,'get_or_create_collection',lambda name: SimpleNamespace(delete=delete))
    monkeypatch.setattr(bm25_index,'add_chunks',add)
    monkeypatch.setattr(bm25_index,'remove_chunks',remove)
    monkeypatch.setattr(ingestion,'chunk_document',lambda doc:[Chunk(f'new-{i}',{}) for i in range(70)])
    monkeypatch.setattr(ingestion,'_embed_chunks',lambda chunks: ([[1.0,0.0] for _ in chunks],'local'))
    monkeypatch.setattr(ingestion,'_is_ai_summary_enabled',lambda:False)
    worker._active_workers.add(tid)
    yield SimpleNamespace(tid=tid,fid=fid,path=path,stored=stored,lexical=lexical,upsert=upsert,delete=delete)
    worker._active_workers.discard(tid)
    for row in pending_runs(tid):
        try: cleanup_run(row['id'])
        except Exception: pass


def test_cancel_during_embedding_preserves_old_and_waits(setup,monkeypatch):
    x=setup
    def embed(chunks):
        assert worker.cancel_task(x.tid)
        assert db.get_upload_task(x.tid)['status']=='cancelling'
        assert db.get_file_by_path(str(x.path),'default')['content_hash']=='old-hash'
        return [[1.0,0.0] for _ in chunks],'local'
    monkeypatch.setattr(ingestion,'_embed_chunks',embed)
    worker._process_task(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cancelled'
    assert set(x.stored)=={'old-chunk'} and set(x.lexical)=={'old-chunk'}
    assert db.get_file_by_path(str(x.path),'default')['content_hash']=='old-hash'
    assert not pending_runs(x.tid) and x.path.exists()


def test_cancel_during_publication_rolls_back_partial_writes(setup,monkeypatch):
    x=setup
    def upsert(chunks,**kwargs):
        assert db.get_file_by_path(str(x.path),'default')['chunk_ids_json']=='["old-chunk"]'
        x.upsert(chunks)
        worker.cancel_task(x.tid)
    monkeypatch.setattr(vector_store,'upsert_chunks',upsert)
    worker._process_task(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cancelled'
    assert set(x.stored)=={'old-chunk'} and set(x.lexical)=={'old-chunk'}
    assert not pending_runs(x.tid)


def test_cleanup_failure_is_visible_and_retryable(setup,monkeypatch):
    x=setup
    def upsert(chunks,**kwargs):
        x.upsert(chunks);worker.cancel_task(x.tid)
    def fail(ids): raise OSError('simulated cleanup failure')
    monkeypatch.setattr(vector_store,'upsert_chunks',upsert)
    monkeypatch.setattr(vector_store,'get_or_create_collection',lambda name:SimpleNamespace(delete=fail))
    worker._process_task(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cleanup_failed'
    assert pending_runs(x.tid)
    assert set(x.stored)-{'old-chunk'} <= db.unpublished_chunk_ids()
    monkeypatch.setattr(vector_store,'get_or_create_collection',lambda name:SimpleNamespace(delete=x.delete))
    worker._finish_cancel(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cancelled'
    assert not pending_runs(x.tid) and set(x.stored)=={'old-chunk'}


def test_success_replaces_old_only_after_preparation(setup):
    x=setup
    worker._process_task(x.tid)
    assert db.get_upload_task(x.tid)['status']=='completed'
    assert 'old-chunk' not in x.stored and len(x.stored)==70
    assert 'old-chunk' not in x.lexical and len(x.lexical)==70
    record=db.get_file_by_path(str(x.path),'default')
    assert record['status']=='done' and record['content_hash']==ingestion._hash_file(x.path)
    assert not pending_runs(x.tid)


def test_cancel_summary_keeps_committed_document(setup,monkeypatch):
    x=setup
    monkeypatch.setattr(ingestion,'_is_ai_summary_enabled',lambda:True)
    monkeypatch.setattr(ingestion,'_ai_summary_min_word_count',lambda:0)
    def summarize(**kwargs):
        assert len(x.stored)==70
        worker.cancel_task(x.tid)
        kwargs['check_cancel']()
        pytest.fail('cancel must stop summary')
    monkeypatch.setattr(ingestion,'summarize_and_extract',summarize)
    worker._process_task(x.tid)
    task=db.get_upload_task(x.tid)
    assert task['status']=='cancelled' and task['done']==1
    assert len(x.stored)==70 and db.get_file_by_path(str(x.path),'default')['status']=='done'


def test_new_file_cancel_removes_pending_record_keeps_source(setup,monkeypatch):
    x=setup
    with db.get_cursor() as cur: cur.execute('DELETE FROM knowledge_files WHERE id=?',(x.fid,))
    x.stored.clear();x.lexical.clear()
    def embed(chunks):
        worker.cancel_task(x.tid)
        return [[1.0,0.0] for _ in chunks],'local'
    monkeypatch.setattr(ingestion,'_embed_chunks',embed)
    worker._process_task(x.tid)
    assert db.get_file_by_path(str(x.path),'default') is None
    assert not x.stored and x.path.exists() and not pending_runs(x.tid)


def test_crash_recovery_cleans_journal_without_touching_old(setup):
    x=setup
    token=current_upload_task.set(x.tid)
    try:
        existing=db.get_file_by_path(str(x.path),'default')
        tx=IngestTransaction(existing=existing,collection_name='test_collection',file_values={
            'relative_path':str(x.path),'absolute_path':str(x.path),'content_hash':'new-hash',
            'file_size':11,'file_type':'md','source_package':None,'kb_id':'default'})
        tx.set_ids(['new-incomplete'])
        tx.append([{'id':'new-incomplete','text':'new','metadata':{},'embedding':[1.0,0.0]}])
        x.stored['new-incomplete']={'id':'new-incomplete'}
        with db.get_cursor() as cur: cur.execute("UPDATE ingest_runs SET status='publishing' WHERE id=?",(tx.id,))
    finally: current_upload_task.reset(token)
    worker.recover_interrupted_tasks()
    assert set(x.stored)=={'old-chunk'} and not tx.spool.exists()
    assert db.get_upload_task(x.tid)['status']=='paused'


def test_summary_checks_cancel_after_model_returns(monkeypatch):
    from src.knowledge import ai_summarizer as ai
    from src.core.ingest_state import TaskCancelledError
    returned=False
    def chat(*args,**kwargs):
        nonlocal returned
        returned=True
        return '{"summary":"summary","concepts":[]}', 'fake'
    def check():
        if returned: raise TaskCancelledError()
    monkeypatch.setattr(ai.llm_client,'get_client',lambda:SimpleNamespace(chat=chat))
    with pytest.raises(TaskCancelledError):
        ai.summarize_and_extract(file_id=1,kb_id='default',file_name='x',chunks=[Chunk('text',{})],
            content_hash='hash',collection_name='none',source_path='none',check_cancel=check)


def test_real_indexes_rollback_and_commit(tmp_path):
    import uuid
    from src.core.ingest_state import TaskCancelledError
    db.init_db()
    path=tmp_path/'real.md';path.write_text('new')
    collection='test-cancel-'+uuid.uuid4().hex
    fid=db.upsert_file(relative_path=str(path),absolute_path=str(path),content_hash='old',
                      file_size=3,file_type='md',kb_id='default')
    old='old-'+uuid.uuid4().hex
    db.set_file_processed(fid,[old])
    payload={'id':old,'text':'original content','metadata':{'file_id':fid,'kb_id':'default'},'embedding':[1.0,0.0]}
    vector_store.upsert_chunks([payload],collection_name=collection)
    bm25_index.add_chunks([payload])
    values={'relative_path':str(path),'absolute_path':str(path),'content_hash':'new','file_size':3,
            'file_type':'md','source_package':None,'kb_id':'default'}
    try:
        for cancel in (True,False):
            tx=IngestTransaction(existing=db.get_file_by_path(str(path),'default'),collection_name=collection,file_values=values)
            cid=uuid.uuid4().hex
            new={'id':cid,'text':'replacement content','metadata':{'file_id':fid,'kb_id':'default'},'embedding':[0.0,1.0]}
            tx.set_ids([cid]);tx.append([new])
            assert [h['id'] for h in vector_store.query_by_embedding([1.0,0.0],k=2,collection_name=collection)]==[old]
            def report(stage,payload):
                if cancel and payload.get('detail')=='确认提交': raise TaskCancelledError()
            if cancel:
                with pytest.raises(TaskCancelledError): tx.publish([cid],[{k:v for k,v in new.items() if k!='embedding'}],report)
                assert set(vector_store.get_chunks_by_ids([old,cid],collection_name=collection))=={old}
                assert db.get_file_by_path(str(path),'default')['content_hash']=='old'
            else:
                tx.publish([cid],[{k:v for k,v in new.items() if k!='embedding'}],report)
                assert set(vector_store.get_chunks_by_ids([old,cid],collection_name=collection))=={cid}
                assert db.get_file_by_path(str(path),'default')['content_hash']=='new'
    finally:
        vector_store.get_chroma_client().delete_collection(collection)


def test_cancel_at_worker_exit_does_not_stay_running(setup,monkeypatch):
    x=setup
    monkeypatch.setattr(worker,'_process_task',lambda tid: worker.cancel_task(tid))
    worker._run_in_thread(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cancelled'
    assert x.tid not in worker._active_workers


def test_cleanup_retry_after_commit_preserves_new_document(setup,monkeypatch):
    x=setup
    def fail(ids): raise OSError('old index cleanup failed')
    monkeypatch.setattr(vector_store,'get_or_create_collection',lambda name:SimpleNamespace(delete=fail))
    worker._process_task(x.tid)
    assert db.get_upload_task(x.tid)['status']=='cleanup_failed'
    assert db.get_file_by_path(str(x.path),'default')['content_hash']==ingestion._hash_file(x.path)
    assert 'old-chunk' in db.unpublished_chunk_ids()
    monkeypatch.setattr(vector_store,'get_or_create_collection',lambda name:SimpleNamespace(delete=x.delete))
    worker._finish_cancel(x.tid)
    assert db.get_upload_task(x.tid)['done']==1
    assert len(x.stored)==70 and 'old-chunk' not in x.stored
