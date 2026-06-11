"""知识库元数据数据库。

SQLite 表：
- knowledge_files: 已投喂文件（hash、状态、来源、对应 chunk_id 列表）
- feedback_queue: 用户点赞/反馈的问答对（待审批）
- knowledge_approved: 审批通过的有效 Q&A（也已写入向量库）

架构约束：
    只在 FastAPI 进程内使用（SQLite 多进程写入会锁）。
    Streamlit 通过 HTTP 调 FastAPI，不直接访问。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from src.core.config import settings


# SQLite 连接需要线程局部
_local = threading.local()


def _get_db_path() -> Path:
    return settings.get_path("metadata_db")


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            _get_db_path(),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def get_cursor() -> Iterator[sqlite3.Cursor]:
    """获取游标。"""
    cur = _get_conn().cursor()
    try:
        yield cur
    finally:
        cur.close()


def init_db() -> None:
    """建表（幂等）。"""
    with get_cursor() as cur:
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relative_path TEXT UNIQUE NOT NULL,
                absolute_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                source_package TEXT,
                extracted_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_kf_hash ON knowledge_files(content_hash);
            CREATE INDEX IF NOT EXISTS idx_kf_status ON knowledge_files(status);

            CREATE TABLE IF NOT EXISTS feedback_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                sources_json TEXT NOT NULL DEFAULT '[]',
                used_provider TEXT,
                rating INTEGER NOT NULL,
                user_comment TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer TEXT,
                review_note TEXT,
                reviewed_at TIMESTAMP,
                knowledge_file_id INTEGER,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (knowledge_file_id) REFERENCES knowledge_files(id)
            );
            CREATE INDEX IF NOT EXISTS idx_fq_status ON feedback_queue(status);

            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


# ===== knowledge_files CRUD =====


def upsert_file(
    relative_path: str,
    absolute_path: str,
    content_hash: str,
    file_size: int,
    file_type: str,
    source_package: str | None = None,
) -> int:
    """新增或更新文件记录（status=pending）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowledge_files
                (relative_path, absolute_path, content_hash, file_size, file_type, source_package, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(relative_path) DO UPDATE SET
                absolute_path=excluded.absolute_path,
                content_hash=excluded.content_hash,
                file_size=excluded.file_size,
                file_type=excluded.file_type,
                source_package=COALESCE(excluded.source_package, knowledge_files.source_package),
                status='pending',
                error_message=NULL,
                chunk_ids_json='[]',
                chunk_count=0,
                processed_at=NULL
            RETURNING id
            """,
            (relative_path, absolute_path, content_hash, file_size, file_type, source_package),
        )
        row = cur.fetchone()
        return int(row["id"])


def set_file_processed(file_id: int, chunk_ids: list[str]) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_files
            SET status='done', chunk_ids_json=?, chunk_count=?, processed_at=?, error_message=NULL
            WHERE id=?
            """,
            (json.dumps(chunk_ids), len(chunk_ids), datetime.utcnow().isoformat(), file_id),
        )


def set_file_failed(file_id: int, error: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE knowledge_files SET status='failed', error_message=?, processed_at=? WHERE id=?",
            (error[:500], datetime.utcnow().isoformat(), file_id),
        )


def get_file_by_path(relative_path: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM knowledge_files WHERE relative_path=?", (relative_path,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_files(status: str | None = None, limit: int = 1000) -> list[dict]:
    with get_cursor() as cur:
        if status:
            cur.execute(
                "SELECT * FROM knowledge_files WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM knowledge_files ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]


def delete_file(relative_path: str) -> dict | None:
    """删除文件记录，返回被删记录（包含 chunk_ids 用于从向量库清理）。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM knowledge_files WHERE relative_path=?",
            (relative_path,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("DELETE FROM knowledge_files WHERE id=?", (row["id"],))
        return dict(row)


def get_stats() -> dict[str, Any]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='done' THEN chunk_count ELSE 0 END) AS total_chunks,
                SUM(CASE WHEN status='done' THEN file_size ELSE 0 END) AS done_size
            FROM knowledge_files
            """
        )
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS c FROM feedback_queue WHERE status='pending'")
        fb = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS c FROM feedback_queue WHERE status='approved'")
        approved = cur.fetchone()
        return {
            "files_total": row["total"] or 0,
            "files_done": row["done"] or 0,
            "files_pending": row["pending"] or 0,
            "files_failed": row["failed"] or 0,
            "total_chunks": row["total_chunks"] or 0,
            "total_size_bytes": row["done_size"] or 0,
            "feedback_pending": fb["c"] or 0,
            "feedback_approved": approved["c"] or 0,
        }


# ===== feedback_queue CRUD =====


def enqueue_feedback(
    question: str,
    answer: str,
    sources: list[dict],
    used_provider: str,
    rating: int,
    user_comment: str = "",
) -> int:
    """加入反馈队列（rating=1 赞，-1 踩）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO feedback_queue
                (question, answer, sources_json, used_provider, rating, user_comment)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (question, answer, json.dumps(sources, ensure_ascii=False), used_provider, rating, user_comment),
        )
        return int(cur.fetchone()["id"])


def list_feedback(status: str = "pending", limit: int = 100) -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM feedback_queue WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_feedback(fid: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM feedback_queue WHERE id=?", (fid,))
        row = cur.fetchone()
        return dict(row) if row else None


def review_feedback(
    fid: int,
    decision: str,
    reviewer: str = "pm",
    note: str = "",
    knowledge_file_id: int | None = None,
) -> None:
    """审批反馈：decision=approved/rejected。"""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision 必须是 approved/rejected，实际: {decision}")
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE feedback_queue
            SET status=?, reviewer=?, review_note=?, reviewed_at=?, knowledge_file_id=?
            WHERE id=?
            """,
            (decision, reviewer, note, datetime.utcnow().isoformat(), knowledge_file_id, fid),
        )


# ===== app_state =====


def get_state(key: str, default: Any = None) -> Any:
    with get_cursor() as cur:
        cur.execute("SELECT value FROM app_state WHERE key=?", (key,))
        row = cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]


def set_state(key: str, value: Any) -> None:
    v = json.dumps(value, ensure_ascii=False)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, v, datetime.utcnow().isoformat()),
        )
