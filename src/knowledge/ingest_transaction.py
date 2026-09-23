"""Durable staging journal; publish new indexes only after all vectors are ready."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

from src.core import vector_store
from src.core.ingest_state import publication_lock, current_upload_task, TaskCancelledError, CleanupError
from src.db import metadata_db as db
from src.qa import bm25_index


def _row(run_id):
    with db.get_cursor() as cur:
        row = cur.execute("SELECT * FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def pending_runs(task_id=None):
    with db.get_cursor() as cur:
        rows = cur.execute("SELECT * FROM ingest_runs" + (" WHERE task_id=?" if task_id else ""),
                           (task_id,) if task_id else ()).fetchall()
        return [dict(row) for row in rows]


def cleanup_run(run_id):
    """Idempotent; keep the journal and all recoverable state if any cleanup fails."""
    row = _row(run_id)
    if not row:
        return
    with publication_lock:
        try:
            committed = row["status"] == "committed"
            ids = json.loads(row["old_ids_json"] if committed else row["new_ids_json"])
            if ids:
                # Do not swallow Chroma lookup/delete errors and claim successful cleanup.
                collection = vector_store.get_or_create_collection(row["collection_name"])
                collection.delete(ids=ids)
                bm25_index.remove_chunks(ids)
            if committed and row["old_hash"]:
                from src.knowledge.ai_summarizer import _make_summary_chunk_id
                old_summary = _make_summary_chunk_id(row["file_id"], row["old_hash"])
                vector_store.get_or_create_collection(row["collection_name"]).delete(ids=[old_summary])
                db.remove_file_from_concepts(row["file_id"])
            if not committed and row.get('reading_artifact'):
                from src.knowledge.reading_index import root
                (root() / row['reading_artifact']).unlink(missing_ok=True)
            spool = Path(row["spool_path"])
            spool.unlink(missing_ok=True)
            with db.get_cursor() as cur:
                cur.execute("BEGIN IMMEDIATE")
                try:
                    cur.execute("DELETE FROM ingest_runs WHERE id=?", (run_id,))
                    if row["was_new"] and not committed:
                        cur.execute("DELETE FROM knowledge_files WHERE id=? AND status='pending'", (row["file_id"],))
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
        except Exception as exc:
            with db.get_cursor() as cur:
                cur.execute("UPDATE ingest_runs SET error=? WHERE id=?", (str(exc)[:1000], run_id))
            raise CleanupError(f"清理未完成，记录已保留，可重试清理：{exc}") from exc


class IngestTransaction:
    def __init__(self, *, existing, collection_name, file_values):
        self.id = uuid.uuid4().hex
        self.file_values = file_values
        self.committed = False
        self.reading = None
        self.spool = db.settings.get_path("metadata_db").parent / "ingest-staging" / (self.id + ".jsonl")
        self.spool.parent.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        with db.get_cursor() as cur:
            cur.execute("BEGIN IMMEDIATE")
            try:
                if existing:
                    self.file_id = existing["id"]
                else:
                    row = cur.execute("""INSERT INTO knowledge_files
                        (relative_path,absolute_path,content_hash,file_size,file_type,source_package,kb_id,status)
                        VALUES (?,?,?,?,?,?,?,'pending') RETURNING id""",
                        tuple(file_values[k] for k in ('relative_path','absolute_path','content_hash','file_size',
                                                       'file_type','source_package','kb_id'))).fetchone()
                    self.file_id = int(row["id"])
                cur.execute("""INSERT INTO ingest_runs
                    (id,file_id,task_id,collection_name,was_new,status,old_ids_json,old_hash,spool_path)
                    VALUES (?,?,?,?,?,'building',?,?,?)""",
                    (self.id,self.file_id,current_upload_task.get(),collection_name,int(existing is None),
                     (existing.get("chunk_ids_json") or "[]") if existing else "[]",
                     existing.get("content_hash") if existing else None,str(self.spool)))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def attach_reading(self, builder):
        self.reading = builder
        with db.get_cursor() as cur:
            cur.execute('UPDATE ingest_runs SET reading_artifact=? WHERE id=?',(builder.name,self.id))

    def set_ids(self, ids):
        with db.get_cursor() as cur:
            cur.execute("UPDATE ingest_runs SET new_ids_json=? WHERE id=?", (json.dumps(ids),self.id))

    def append(self, payloads):
        with self.spool.open("a",encoding="utf-8") as stream:
            for payload in payloads:
                stream.write(json.dumps(payload,ensure_ascii=False) + "\n")

    def publish(self, ids, text_payloads, progress):
        with publication_lock:
            progress("publishing", {"detail": "提交新索引，原索引保留至提交成功"})
            with db.get_cursor() as cur:
                cur.execute("UPDATE ingest_runs SET status='publishing' WHERE id=?", (self.id,))
            try:
                if self.spool.exists():
                    with self.spool.open(encoding="utf-8") as stream:
                        batch = []
                        written = 0
                        for line in stream:
                            batch.append(json.loads(line))
                            if len(batch) == 32:
                                progress("publishing", {"detail": "写入新索引", "completed": written, "total": len(ids)})
                                vector_store.upsert_chunks(batch,collection_name=self.collection_name)
                                written += len(batch)
                                batch.clear()
                        if batch:
                            progress("publishing", {"detail": "写入新索引", "completed": written, "total": len(ids)})
                            vector_store.upsert_chunks(batch,collection_name=self.collection_name)
                progress("publishing", {"detail": "更新关键词索引"})
                bm25_index.add_chunks(text_payloads)
                progress("publishing", {"detail": "确认提交"})
                # Cancellation and publication serialize through the same SQLite transaction.
                with db.get_cursor() as cur:
                    cur.execute("BEGIN IMMEDIATE")
                    try:
                        tid = current_upload_task.get()
                        task = cur.execute("SELECT status FROM upload_tasks WHERE id=?", (tid,)).fetchone() if tid else None
                        if tid and (not task or task["status"] in ('cancelling','cleaning','cancelled')):
                            raise TaskCancelledError()
                        values = self.file_values
                        cur.execute("""UPDATE knowledge_files SET absolute_path=?,content_hash=?,file_size=?,file_type=?,
                            source_package=?,status='done',chunk_ids_json=?,chunk_count=?,processed_at=?,error_message=NULL
                            WHERE id=?""", (values['absolute_path'],values['content_hash'],values['file_size'],values['file_type'],
                            values['source_package'],json.dumps(ids),len(ids),datetime.now(timezone.utc).isoformat(),self.file_id))
                        if self.reading:
                            self.reading.publish(cur, self.file_id, values['content_hash'])
                        cur.execute("UPDATE document_meta SET summary=NULL, processed_level='raw' WHERE file_id=?",(self.file_id,))
                        if tid:
                            cur.execute("UPDATE upload_task_files SET skip_reason='main_import_completed' WHERE task_id=? AND absolute_path=? AND status='processing'",
                                        (tid, values['absolute_path']))
                        cur.execute("UPDATE ingest_runs SET status='committed',error=NULL WHERE id=?",(self.id,))
                        cur.execute("COMMIT")
                    except Exception:
                        cur.execute("ROLLBACK")
                        raise
                self.committed = True
                cleanup_run(self.id)
            except BaseException as exc:
                # Rollback visibility before allowing queries to resume.
                if not self.committed:
                    try:
                        if isinstance(exc, TaskCancelledError):
                            progress("cleaning", {"detail": "正在回滚本次未完成的索引"})
                    except TaskCancelledError:
                        pass
                    finally:
                        cleanup_run(self.id)
                raise

    def abort(self):
        if self.reading:
            self.reading.finish()
        cleanup_run(self.id)
