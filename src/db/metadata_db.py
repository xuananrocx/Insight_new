"""知识库元数据数据库。

SQLite 表：
- knowledge_files: 已投喂文件（hash、状态、来源、对应 chunk_id 列表）
- feedback_queue: 用户点赞/反馈的问答对（待审批）
- knowledge_approved: 审批通过的有效 Q&A（也已写入向量库）
- app_state: 应用 KV 状态

架构约束：
    只在 FastAPI 进程内使用（SQLite 多进程写入会锁）。
    Streamlit 通过 HTTP 调 FastAPI，不直接访问。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from src.core.config import settings


# SQLite 连接：每次操作开新连接（避免 threading.local 在 WAL 模式下跨线程读到 stale snapshot）
# 当前 schema 版本（每次表结构变更 +1）
SCHEMA_VERSION = 13


# ===== 哨兵：区分「不更新」和「清空为 NULL」 =====
# 问题背景：update_xxx 函数之前用 None 同时表示「调用方没传」和「清空字段」，
#          导致无法把字段清空为 NULL（例如清空 turn.error）。
# 解决：用 _UNSET 哨兵表示「不更新」，None 才真正表示「清空」。
class _Unset:
    """哨兵类型。直接用单例做类型注解友好。"""

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _Unset()


def _get_db_path() -> Path:
    return settings.get_path("metadata_db")


@contextmanager
def get_cursor() -> Iterator[sqlite3.Cursor]:
    """每次调用开一个新连接，用完关闭。

    SQLite 连接开销很小（毫秒级），但能避免 threading.local 在 WAL 模式下
    缓存旧 snapshot 导致跨线程读不到最新数据的问题。
    """
    conn = sqlite3.connect(
        _get_db_path(),
        check_same_thread=False,
        isolation_level=None,  # autocommit
        timeout=5.0,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
    finally:
        conn.close()


# ===== Schema 迁移 =====

# 迁移函数表：key = 起始版本，value = 迁移函数
_SCHEMA_MIGRATIONS: dict[int, Callable[[sqlite3.Cursor], None]] = {}


def _register_schema_migration(from_version: int):
    """装饰器：注册 schema 迁移函数。"""
    def decorator(fn: Callable[[sqlite3.Cursor], None]) -> Callable[[sqlite3.Cursor], None]:
        _SCHEMA_MIGRATIONS[from_version] = fn
        return fn
    return decorator


def _get_user_version(cur: sqlite3.Cursor) -> int:
    cur.execute("PRAGMA user_version")
    return int(cur.fetchone()[0])


def _set_user_version(cur: sqlite3.Cursor, version: int) -> None:
    cur.execute(f"PRAGMA user_version = {version}")


# ===== 启动备份 =====

def _backup_db() -> None:
    """启动时把 metadata.db 备份成 .bak（覆盖旧的，只保留 1 个）。"""
    db_path = _get_db_path()
    if not db_path.exists() or db_path.stat().st_size < 1024:
        return
    bak_path = db_path.with_suffix(db_path.suffix + ".bak")
    try:
        shutil.copy2(db_path, bak_path)
    except Exception as e:
        # 备份失败不致命，但要让用户看到（日志）
        import logging
        logging.getLogger(__name__).warning(f"备份 metadata.db 失败: {e}")


# ===== 建表 + 迁移 =====

def init_db() -> None:
    """建表 + 跑 schema 迁移（幂等）。"""
    _backup_db()
    with get_cursor() as cur:
        # v0：首次建表（CREATE IF NOT EXISTS 保证幂等）
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                source_package TEXT,
                kb_id TEXT NOT NULL DEFAULT 'default',
                extracted_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (kb_id) REFERENCES kbs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_kf_hash ON knowledge_files(content_hash);
            CREATE INDEX IF NOT EXISTS idx_kf_status ON knowledge_files(status);
            CREATE INDEX IF NOT EXISTS idx_kf_kb_id ON knowledge_files(kb_id);
            -- 复合唯一约束：(kb_id, relative_path) 防止跨 KB 同名偷偷迁移 kb_id
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kf_kb_relpath ON knowledge_files(kb_id, relative_path);

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

            CREATE TABLE IF NOT EXISTS kbs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                collection_name TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                embedding_model TEXT,
                embedding_dim INTEGER,
                is_default INTEGER NOT NULL DEFAULT 0,
                retrieval_strategy TEXT NOT NULL DEFAULT 'basic',
                global_summary TEXT,
                global_summary_model TEXT,
                global_summary_tokens INTEGER,
                global_summary_created_at INTEGER,
                global_summary_snapshot TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kbs_source ON kbs(source);
            CREATE INDEX IF NOT EXISTS idx_kbs_default ON kbs(is_default);

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                kb_scope TEXT,
                retrieval_mode TEXT NOT NULL DEFAULT 'ai'
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);

            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                order_idx INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                sources_json TEXT NOT NULL DEFAULT '[]',
                trace_json TEXT NOT NULL DEFAULT '[]',
                used_provider TEXT,
                liked INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'ai'
            );
            CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, order_idx);

            -- v4: 文档级元信息（1:1 关联 knowledge_files）
            CREATE TABLE IF NOT EXISTS document_meta (
                file_id INTEGER PRIMARY KEY,
                kb_id TEXT NOT NULL DEFAULT 'default',
                processed_level TEXT NOT NULL DEFAULT 'raw',
                summary TEXT,
                summary_model TEXT,
                summary_created_at INTEGER,
                summary_tokens INTEGER,
                concepts_extracted INTEGER NOT NULL DEFAULT 0,
                concepts_count INTEGER NOT NULL DEFAULT 0,
                doc_type TEXT,
                section_count INTEGER,
                total_chunks INTEGER,
                word_count INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (file_id) REFERENCES knowledge_files(id),
                FOREIGN KEY (kb_id) REFERENCES kbs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_document_meta_kb_id ON document_meta(kb_id);
            CREATE INDEX IF NOT EXISTS idx_document_meta_level ON document_meta(processed_level);

            -- v4: KB 级概念（预留，迭代 5 用）
            CREATE TABLE IF NOT EXISTS kb_concepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_id TEXT NOT NULL,
                concept_name TEXT NOT NULL,
                concept_type TEXT,
                description TEXT,
                source_file_ids_json TEXT NOT NULL DEFAULT '[]',
                mention_count INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (kb_id) REFERENCES kbs(id),
                UNIQUE (kb_id, concept_name)
            );
            CREATE INDEX IF NOT EXISTS idx_kb_concepts_kb_id ON kb_concepts(kb_id);
            CREATE INDEX IF NOT EXISTS idx_kb_concepts_type ON kb_concepts(concept_type);

            -- v4: 概念关联（预留，迭代 6 用）
            CREATE TABLE IF NOT EXISTS concept_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_id TEXT NOT NULL,
                source_concept_id INTEGER NOT NULL,
                target_concept_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (kb_id) REFERENCES kbs(id),
                FOREIGN KEY (source_concept_id) REFERENCES kb_concepts(id),
                FOREIGN KEY (target_concept_id) REFERENCES kb_concepts(id),
                UNIQUE (source_concept_id, target_concept_id, relation_type)
            );
            CREATE INDEX IF NOT EXISTS idx_concept_relations_kb_id ON concept_relations(kb_id);
            CREATE INDEX IF NOT EXISTS idx_concept_relations_source ON concept_relations(source_concept_id);
            CREATE INDEX IF NOT EXISTS idx_concept_relations_target ON concept_relations(target_concept_id);

            -- v8: 批量上传任务（持久化，支持断点续传）
            CREATE TABLE IF NOT EXISTS upload_tasks (
                id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL,
                skip_mode TEXT NOT NULL DEFAULT 'skip',
                auto_ingest INTEGER NOT NULL DEFAULT 1,
                total INTEGER NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                upload_complete INTEGER NOT NULL DEFAULT 1,
                current_file_path TEXT,
                current_stage TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                finished_at INTEGER,
                FOREIGN KEY (kb_id) REFERENCES kbs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_upload_tasks_status ON upload_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_upload_tasks_kb_id ON upload_tasks(kb_id);

            -- v8: 批量任务的单文件状态（子表）
            CREATE TABLE IF NOT EXISTS upload_task_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                skip_reason TEXT,
                error_message TEXT,
                started_at INTEGER,
                finished_at INTEGER,
                FOREIGN KEY (task_id) REFERENCES upload_tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_upload_task_files_task ON upload_task_files(task_id);
            CREATE INDEX IF NOT EXISTS idx_upload_task_files_status ON upload_task_files(status);

            -- v9: AI 调用日志（每次 LLM 调用的完整 prompt + messages + response）
            CREATE TABLE IF NOT EXISTS ai_call_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT,
                scene TEXT NOT NULL,
                session_id TEXT,
                turn_id TEXT,
                kb_id TEXT,
                system_prompt TEXT,
                messages_json TEXT NOT NULL,
                response_text TEXT,
                duration_ms INTEGER,
                success INTEGER NOT NULL,
                error_message TEXT,
                token_input INTEGER,
                token_output INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_call_logs_provider ON ai_call_logs(provider);
            CREATE INDEX IF NOT EXISTS idx_ai_call_logs_scene ON ai_call_logs(scene);
            CREATE INDEX IF NOT EXISTS idx_ai_call_logs_created ON ai_call_logs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ai_call_logs_session ON ai_call_logs(session_id);
            """
        )

        # 版本化迁移
        current = _get_user_version(cur)
        if current == 0:
            # 全新建库直接落在最新版本、不跑迁移链，
            # 默认知识库必须在这里显式播种
            _ensure_default_kb(cur)
            _set_user_version(cur, SCHEMA_VERSION)
            current = SCHEMA_VERSION

    # 迁移用独立连接 + 显式事务，保证单次迁移的原子性。
    # 注意：必须用 execute() 序列而非 executescript()，因为 executescript
    # 会先隐式 COMMIT 当前事务，破坏事务包裹。
    if current < SCHEMA_VERSION:
        _run_migrations_with_tx(current)


def _run_migrations_with_tx(from_version: int) -> None:
    """逐个迁移函数用事务包裹。

    每个迁移函数是一个原子单元：成功 → COMMIT + 版本号 +1；失败 → ROLLBACK，
    数据库保持迁移前状态，下次启动可重试。
    """
    import logging
    logger = logging.getLogger(__name__)

    conn = sqlite3.connect(
        _get_db_path(),
        check_same_thread=False,
        isolation_level="DEFERRED",  # 启用 Python 层事务管理
        timeout=5.0,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.cursor()
        current = from_version
        try:
            while current < SCHEMA_VERSION:
                fn = _SCHEMA_MIGRATIONS.get(current)
                if fn is None:
                    break
                logger.info(f"运行 schema 迁移: v{current} → v{current + 1}")
                try:
                    cur.execute("BEGIN IMMEDIATE")
                    try:
                        fn(cur)
                        _set_user_version(cur, current + 1)
                        conn.commit()
                        current += 1
                    except Exception:
                        conn.rollback()
                        raise
                except Exception as e:
                    logger.error(f"schema 迁移 v{current} → v{current + 1} 失败: {e}")
                    raise
        finally:
            cur.close()
    finally:
        conn.close()


# ===== v1 → v2 迁移：sessions + turns 表 =====

@_register_schema_migration(1)
def _migrate_v1_to_v2(cur: sqlite3.Cursor) -> None:
    """v1 → v2：添加 sessions 和 turns 表（用于会话 SQLite 持久化）。

    表结构跟 init_db 里的 CREATE IF NOT EXISTS 完全一致（首次升级时表已存在，幂等）。
    这个迁移函数主要为后续版本兼容性记录——v1 用户升级到 v2 时确保表存在。
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            kb_scope TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)"
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            order_idx INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            sources_json TEXT NOT NULL DEFAULT '[]',
            trace_json TEXT NOT NULL DEFAULT '[]',
            used_provider TEXT,
            liked INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, order_idx)"
    )


# ===== v2 → v3 迁移：kbs 表 + KB 抽象化 =====


def _ensure_default_kb(cur: sqlite3.Cursor) -> None:
    """幂等插入内置知识库（id='default'）。

    全新建库不走迁移链（v0 直接跳到最新版本），所以新建和
    v9 → v10 迁移都要显式调用本函数。
    """
    import time

    cur.execute(
        """
        INSERT OR IGNORE INTO kbs (id, name, description, collection_name, source, is_default, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "default",
            "内置知识库",
            "系统预置的内置知识库",
            "knowledge_base",
            "builtin",
            1,
            int(time.time() * 1000),
            int(time.time() * 1000),
        ),
    )


@_register_schema_migration(2)
def _migrate_v2_to_v3(cur: sqlite3.Cursor) -> None:
    """v2 → v3：添加 kbs 表 + knowledge_files.kb_id + 默认 KB 迁移。

    变更：
    1. 新增 kbs 表（知识库实体）
    2. knowledge_files 表加 kb_id 字段（默认 'default'）
    3. 插入默认 KB（如果不存在）
    4. sessions.kb_scope 为 NULL 的填 'default'
    """
    # 1. 创建 kbs 表
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kbs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            collection_name TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            embedding_model TEXT,
            embedding_dim INTEGER,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kbs_source ON kbs(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kbs_default ON kbs(is_default)")

    # 2. 插入默认 KB（如果不存在）
    _ensure_default_kb(cur)

    # 3. knowledge_files 表加 kb_id 字段（如果还没有）
    # 先检查列是否存在
    cur.execute("PRAGMA table_info(knowledge_files)")
    columns = [row["name"] for row in cur.fetchall()]
    if "kb_id" not in columns:
        cur.execute("ALTER TABLE knowledge_files ADD COLUMN kb_id TEXT NOT NULL DEFAULT 'default'")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kf_kb_id ON knowledge_files(kb_id)")

    # 4. sessions.kb_scope 为 NULL 的填 'default'
    cur.execute("UPDATE sessions SET kb_scope='default' WHERE kb_scope IS NULL")


# ===== v3 → v4 迁移：document_meta + kb_concepts + concept_relations 表 =====

@_register_schema_migration(3)
def _migrate_v3_to_v4(cur: sqlite3.Cursor) -> None:
    """v3 → v4：添加文档级元信息表 + KB 概念表 + 概念关联表。

    为 RAG 升级（AI 摘要层 + 概念层）准备存储结构。
    所有新表都是空的，无需数据迁移。
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS document_meta (
            file_id INTEGER PRIMARY KEY,
            kb_id TEXT NOT NULL DEFAULT 'default',
            processed_level TEXT NOT NULL DEFAULT 'raw',
            summary TEXT,
            summary_model TEXT,
            summary_created_at INTEGER,
            summary_tokens INTEGER,
            concepts_extracted INTEGER NOT NULL DEFAULT 0,
            concepts_count INTEGER NOT NULL DEFAULT 0,
            doc_type TEXT,
            section_count INTEGER,
            total_chunks INTEGER,
            word_count INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (file_id) REFERENCES knowledge_files(id),
            FOREIGN KEY (kb_id) REFERENCES kbs(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_meta_kb_id ON document_meta(kb_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_document_meta_level ON document_meta(processed_level)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_concepts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kb_id TEXT NOT NULL,
            concept_name TEXT NOT NULL,
            concept_type TEXT,
            description TEXT,
            source_file_ids_json TEXT NOT NULL DEFAULT '[]',
            mention_count INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            FOREIGN KEY (kb_id) REFERENCES kbs(id),
            UNIQUE (kb_id, concept_name)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kb_concepts_kb_id ON kb_concepts(kb_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_kb_concepts_type ON kb_concepts(concept_type)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS concept_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kb_id TEXT NOT NULL,
            source_concept_id INTEGER NOT NULL,
            target_concept_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            FOREIGN KEY (kb_id) REFERENCES kbs(id),
            FOREIGN KEY (source_concept_id) REFERENCES kb_concepts(id),
            FOREIGN KEY (target_concept_id) REFERENCES kb_concepts(id),
            UNIQUE (source_concept_id, target_concept_id, relation_type)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_concept_relations_kb_id ON concept_relations(kb_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_concept_relations_source ON concept_relations(source_concept_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_concept_relations_target ON concept_relations(target_concept_id)")


# ===== v4 → v5 迁移：kbs 表加 retrieval_strategy 字段 =====

@_register_schema_migration(4)
def _migrate_v4_to_v5(cur: sqlite3.Cursor) -> None:
    """v4 → v5：kbs 表加 retrieval_strategy 字段（每 KB 一个检索策略）。

    可选值：'basic'（默认，原文 chunk 检索）/ 'summary'（摘要增强检索）。
    """
    # 检查列是否已存在（防幂等失败）
    cur.execute("PRAGMA table_info(kbs)")
    columns = [row["name"] for row in cur.fetchall()]
    if "retrieval_strategy" not in columns:
        cur.execute(
            "ALTER TABLE kbs ADD COLUMN retrieval_strategy TEXT NOT NULL DEFAULT 'basic'"
        )


# ===== v5 → v6 迁移：kbs 表加 global_summary 字段（迭代 6）=====

@_register_schema_migration(5)
def _migrate_v5_to_v6(cur: sqlite3.Cursor) -> None:
    """v5 → v6：kbs 表加 global_summary 系列字段。

    用于存储 KB 级 AI 全局摘要（融合所有文档摘要生成）。
    """
    cur.execute("PRAGMA table_info(kbs)")
    columns = [row["name"] for row in cur.fetchall()]
    additions = [
        ("global_summary", "TEXT"),
        ("global_summary_model", "TEXT"),
        ("global_summary_tokens", "INTEGER"),
        ("global_summary_created_at", "INTEGER"),
    ]
    for col, sqltype in additions:
        if col not in columns:
            cur.execute(f"ALTER TABLE kbs ADD COLUMN {col} {sqltype}")


@_register_schema_migration(6)
def _migrate_v6_to_v7(cur: sqlite3.Cursor) -> None:
    """v6 → v7：knowledge_files 把 relative_path UNIQUE 改为 (kb_id, relative_path) 复合唯一。

    背景：原 schema 的 `relative_path UNIQUE` 在跨 KB 同 relative_path 场景下，
    ON CONFLICT 会偷偷覆盖 kb_id（数据被错误迁移到新 KB）。
    修复：改用复合 UNIQUE，让 (KB-A, "test.md") 和 (KB-B, "test.md") 可以共存。

    SQLite 不支持 ALTER TABLE 改约束，需要重建表（CREATE NEW → INSERT SELECT → DROP OLD → RENAME）。
    """
    # 幂等检查：用 schema 元数据判断是否已迁移
    # 判据：
    #   - 如果 knowledge_files 表已有"单列 UNIQUE 索引"（即 relative_path UNIQUE 约束的索引） → 未迁移
    #   - 否则（只有复合索引 idx_kf_kb_relpath）→ 已迁移
    # 注意：不能用 sqlite_master.sql 文本匹配（格式化差异会失败）；
    #       也不能用 idx_kf_kb_relpath 是否存在判断（init_db 的 CREATE INDEX IF NOT EXISTS 会抢先建）。
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_files'"
    )
    if not cur.fetchone():
        return  # 表不存在，无需迁移

    cur.execute("PRAGMA index_list(knowledge_files)")
    has_single_col_unique = False
    for idx_row in cur.fetchall():
        # idx_row: (seq, name, unique, origin, partial)
        if idx_row["unique"] != 1:
            continue
        idx_name = idx_row["name"]
        if idx_name == "idx_kf_kb_relpath":
            continue  # 这是复合索引（目标态），跳过
        # 检查这个 UNIQUE 索引是不是单列（relative_path）
        cur.execute(f"PRAGMA index_info({idx_name})")
        cols_in_idx = [r["name"] for r in cur.fetchall()]
        if len(cols_in_idx) == 1 and "relative_path" in cols_in_idx:
            has_single_col_unique = True
            break

    if not has_single_col_unique:
        return  # 已迁移过（没有单列 UNIQUE 索引）

    # 备份原表数据
    cur.execute("PRAGMA table_info(knowledge_files)")
    columns_info = cur.fetchall()
    column_names = [c["name"] for c in columns_info]

    # 创建新表（结构跟原表一致，但去掉 relative_path 的 UNIQUE 约束）
    # 临时禁用外键检查（DROP TABLE 会被 FK 约束阻止）
    cur.execute("PRAGMA foreign_keys=OFF")
    try:
        cur.execute(
            """
            CREATE TABLE knowledge_files_v7 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_type TEXT NOT NULL,
                source_package TEXT,
                kb_id TEXT NOT NULL DEFAULT 'default',
                extracted_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending',
                chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (kb_id) REFERENCES kbs(id)
            )
            """
        )

        # 复制数据：新表字段是原表字段的子集（没有 updated_at），按交集列复制
        new_cols = ["id", "relative_path", "absolute_path", "content_hash", "file_size",
                    "file_type", "source_package", "kb_id", "extracted_at", "status",
                    "chunk_ids_json", "chunk_count", "error_message", "processed_at", "created_at"]
        common = [c for c in new_cols if c in column_names]
        cols_csv = ", ".join(common)
        cur.execute(
            f"INSERT INTO knowledge_files_v7 ({cols_csv}) SELECT {cols_csv} FROM knowledge_files"
        )

        # 删旧表，重命名新表
        cur.execute("DROP TABLE knowledge_files")
        cur.execute("ALTER TABLE knowledge_files_v7 RENAME TO knowledge_files")

        # 重建索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kf_hash ON knowledge_files(content_hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kf_status ON knowledge_files(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kf_kb_id ON knowledge_files(kb_id)")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kf_kb_relpath ON knowledge_files(kb_id, relative_path)"
        )
    finally:
        cur.execute("PRAGMA foreign_keys=ON")


# ===== v7 → v8 迁移：批量上传任务表 =====

@_register_schema_migration(7)
def _migrate_v7_to_v8(cur: sqlite3.Cursor) -> None:
    """v7 → v8：添加批量上传任务表（upload_tasks + upload_task_files）。

    用于支持：
    - 多文件 / 文件夹递归批量上传
    - 进度持久化（刷新页面/重启后可恢复）
    - 同名去重（skip / overwrite 模式）
    - 失败重试

    所有新表都是空的，无需数据迁移。
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_tasks (
            id TEXT PRIMARY KEY,
            kb_id TEXT NOT NULL,
            skip_mode TEXT NOT NULL DEFAULT 'skip',
            auto_ingest INTEGER NOT NULL DEFAULT 1,
            total INTEGER NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            skipped INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            current_file_path TEXT,
            current_stage TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            finished_at INTEGER,
            FOREIGN KEY (kb_id) REFERENCES kbs(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_tasks_status ON upload_tasks(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_tasks_kb_id ON upload_tasks(kb_id)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_task_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            absolute_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            skip_reason TEXT,
            error_message TEXT,
            started_at INTEGER,
            finished_at INTEGER,
            FOREIGN KEY (task_id) REFERENCES upload_tasks(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_task_files_task ON upload_task_files(task_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_task_files_status ON upload_task_files(status)")


# ===== v8 → v9 迁移：AI 调用日志表 =====

@_register_schema_migration(8)
def _migrate_v8_to_v9(cur: sqlite3.Cursor) -> None:
    """v8 → v9：添加 ai_call_logs 表（每次 LLM 调用的完整日志）。

    用于：
    - 审计：谁在什么时候用了哪个 provider/model
    - 调试：失败的调用看完整 prompt + response
    - 复盘：对比不同 provider 的回答质量
    - 统计：调用次数、token 用量、成功率

    默认保留 30 天（cleanup_ai_call_logs 函数自动清理）。
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT,
            scene TEXT NOT NULL,
            session_id TEXT,
            turn_id TEXT,
            kb_id TEXT,
            system_prompt TEXT,
            messages_json TEXT NOT NULL,
            response_text TEXT,
            duration_ms INTEGER,
            success INTEGER NOT NULL,
            error_message TEXT,
            token_input INTEGER,
            token_output INTEGER,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_call_logs_provider ON ai_call_logs(provider)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_call_logs_scene ON ai_call_logs(scene)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_call_logs_created ON ai_call_logs(created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_call_logs_session ON ai_call_logs(session_id)")


@_register_schema_migration(9)
def _migrate_v9_to_v10(cur: sqlite3.Cursor) -> None:
    """v9 → v10：补插内置知识库。

    全新建库时 v0 直接跳到最新版本、不跑迁移链，导致 kbs 表
    建出来是空的（默认知识库只在 v2 → v3 迁移里插过）。
    本迁移修复已存在的空库；新建库的播种见 init_db。
    """
    _ensure_default_kb(cur)


@_register_schema_migration(10)
def _migrate_v10_to_v11(cur: sqlite3.Cursor) -> None:
    """v10 → v11：upload_tasks 加 upload_complete 列（分批上传支持）。

    0 = 还有批次未传完（任务 status='uploading'），1 = 全部到位。
    存量任务都是一次性建好的，默认 1。列已存在时跳过（幂等）。
    """
    cols = [r[1] for r in cur.execute("PRAGMA table_info(upload_tasks)").fetchall()]
    if "upload_complete" not in cols:
        cur.execute(
            "ALTER TABLE upload_tasks ADD COLUMN upload_complete INTEGER NOT NULL DEFAULT 1"
        )


@_register_schema_migration(11)
def _migrate_v11_to_v12(cur: sqlite3.Cursor) -> None:
    """v11 → v12：kbs 加 global_summary_snapshot 列（摘要过期检测）。

    存 JSON：{file_id(str): content_hash}，生成全局摘要时的文件快照。
    与当前 KB 文件集比对，不一致 → 摘要可能过期。
    """
    cols = [r[1] for r in cur.execute("PRAGMA table_info(kbs)").fetchall()]
    if "global_summary_snapshot" not in cols:
        cur.execute("ALTER TABLE kbs ADD COLUMN global_summary_snapshot TEXT")


@_register_schema_migration(12)
def _migrate_v12_to_v13(cur: sqlite3.Cursor) -> None:
    """v12 → v13：检索模式从 KB 级改到会话级。

    - sessions.retrieval_mode：会话当前检索模式（basic/deep/ai，默认 ai）
    - turns.mode：该轮消息的形态（检索结果 or AI 回答），旧数据全部视为 ai
    - kbs.retrieval_strategy 废弃（列保留作遗留数据，不再读写）
    """
    s_cols = [r[1] for r in cur.execute("PRAGMA table_info(sessions)").fetchall()]
    if "retrieval_mode" not in s_cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN retrieval_mode TEXT NOT NULL DEFAULT 'ai'")
    t_cols = [r[1] for r in cur.execute("PRAGMA table_info(turns)").fetchall()]
    if "mode" not in t_cols:
        cur.execute("ALTER TABLE turns ADD COLUMN mode TEXT NOT NULL DEFAULT 'ai'")


# ===== document_meta CRUD =====


def upsert_document_meta(
    file_id: int,
    kb_id: str,
    *,
    processed_level: str = "raw",
    doc_type: str | None = None,
    section_count: int | None = None,
    total_chunks: int | None = None,
    word_count: int | None = None,
    summary: str | None = None,
    summary_model: str | None = None,
    summary_tokens: int | None = None,
    concepts_extracted: int | None = None,
    concepts_count: int | None = None,
) -> None:
    """插入或更新 document_meta 行（1:1 关联 knowledge_files）。

    首次调用：插入新行；后续调用：更新非 None 字段。
    """
    import time
    now_ms = int(time.time() * 1000)

    with get_cursor() as cur:
        cur.execute("SELECT file_id FROM document_meta WHERE file_id=?", (file_id,))
        exists = cur.fetchone() is not None

        if not exists:
            # 首次插入：用所有字段（None 字段用默认值）
            cur.execute(
                """
                INSERT INTO document_meta (
                    file_id, kb_id, processed_level,
                    summary, summary_model, summary_created_at, summary_tokens,
                    concepts_extracted, concepts_count,
                    doc_type, section_count, total_chunks, word_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id, kb_id, processed_level,
                    summary, summary_model, now_ms if summary else None, summary_tokens,
                    concepts_extracted or 0, concepts_count or 0,
                    doc_type, section_count, total_chunks, word_count,
                    now_ms, now_ms,
                ),
            )
        else:
            # 更新：动态构造 SET 子句（只更新非 None 字段）
            updates = []
            params = []
            for col, val in [
                ("kb_id", kb_id),
                ("processed_level", processed_level),
                ("doc_type", doc_type),
                ("section_count", section_count),
                ("total_chunks", total_chunks),
                ("word_count", word_count),
                ("summary", summary),
                ("summary_model", summary_model),
                ("summary_tokens", summary_tokens),
                ("concepts_extracted", concepts_extracted),
                ("concepts_count", concepts_count),
            ]:
                if val is not None:
                    updates.append(f"{col}=?")
                    params.append(val)
            # 如果有摘要更新，刷新 summary_created_at
            if summary is not None:
                updates.append("summary_created_at=?")
                params.append(now_ms)
            updates.append("updated_at=?")
            params.append(now_ms)
            params.append(file_id)
            cur.execute(
                f"UPDATE document_meta SET {', '.join(updates)} WHERE file_id=?",
                params,
            )


def get_document_meta(file_id: int) -> dict | None:
    """读取单文件的 document_meta。"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM document_meta WHERE file_id=?", (file_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_document_meta_by_kb(
    kb_id: str, level: str | None = None
) -> list[dict]:
    """列出某 KB 下所有 document_meta（可按 processed_level 过滤）。"""
    with get_cursor() as cur:
        if level:
            cur.execute(
                "SELECT * FROM document_meta WHERE kb_id=? AND processed_level=? ORDER BY file_id",
                (kb_id, level),
            )
        else:
            cur.execute(
                "SELECT * FROM document_meta WHERE kb_id=? ORDER BY file_id",
                (kb_id,),
            )
        return [dict(row) for row in cur.fetchall()]


def update_document_summary(
    file_id: int, summary: str, model: str, tokens: int
) -> None:
    """更新文档摘要（迭代 3 用，单独提供方便调用）。"""
    import time
    now_ms = int(time.time() * 1000)
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE document_meta
            SET summary=?, summary_model=?, summary_tokens=?,
                summary_created_at=?, processed_level='summarized',
                updated_at=?
            WHERE file_id=?
            """,
            (summary, model, tokens, now_ms, now_ms, file_id),
        )


def delete_document_meta(file_id: int) -> None:
    """删除单文件的 document_meta（删 knowledge_files 时级联调用）。"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM document_meta WHERE file_id=?", (file_id,))


def delete_document_meta_by_kb(kb_id: str) -> int:
    """删除某 KB 下所有 document_meta（删 KB 时级联调用）。

    Returns: 删除的行数
    """
    with get_cursor() as cur:
        cur.execute("DELETE FROM document_meta WHERE kb_id=?", (kb_id,))
        return cur.rowcount


def delete_concept_data_by_kb(kb_id: str) -> tuple[int, int]:
    """删除某 KB 下所有概念 + 关联（删 KB 时级联调用）。

    Returns: (删除的 concepts 数, 删除的 relations 数)
    """
    with get_cursor() as cur:
        # 先删关联（依赖 concepts）
        cur.execute("DELETE FROM concept_relations WHERE kb_id=?", (kb_id,))
        relations_deleted = cur.rowcount
        cur.execute("DELETE FROM kb_concepts WHERE kb_id=?", (kb_id,))
        concepts_deleted = cur.rowcount
        return concepts_deleted, relations_deleted


# ===== kb_concepts CRUD =====


def upsert_kb_concept(
    kb_id: str,
    concept_name: str,
    *,
    concept_type: str | None = None,
    description: str | None = None,
    source_file_id: int | None = None,
) -> int:
    """插入或更新一个 KB 概念（kb_id + concept_name 唯一）。

    - 概念已存在：mention_count += 1，把 source_file_id 追加到 source_file_ids_json
    - 概念不存在：插入新行，mention_count=1

    Returns: 概念行的 id
    """
    import time
    now_ms = int(time.time() * 1000)

    with get_cursor() as cur:
        cur.execute(
            "SELECT id, source_file_ids_json, mention_count FROM kb_concepts WHERE kb_id=? AND concept_name=?",
            (kb_id, concept_name),
        )
        row = cur.fetchone()

        if row is None:
            # 插入新概念
            source_ids = [source_file_id] if source_file_id is not None else []
            cur.execute(
                """
                INSERT INTO kb_concepts (
                    kb_id, concept_name, concept_type, description,
                    source_file_ids_json, mention_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    kb_id, concept_name, concept_type, description,
                    json.dumps(source_ids), 1, now_ms, now_ms,
                ),
            )
            return int(cur.fetchone()["id"])
        else:
            # 更新现有概念
            cid = int(row["id"])
            try:
                existing_sources: list[int] = json.loads(row["source_file_ids_json"] or "[]")
            except Exception:
                existing_sources = []
            is_new_mention = (
                source_file_id is not None and source_file_id not in existing_sources
            )
            if is_new_mention:
                existing_sources.append(source_file_id)
            new_mention = int(row["mention_count"]) + (1 if is_new_mention else 0)

            # 动态更新（type/description 为 None 时不覆盖）
            updates = [
                "source_file_ids_json=?",
                "mention_count=?",
                "updated_at=?",
            ]
            params: list[Any] = [json.dumps(existing_sources), new_mention, now_ms]
            if concept_type is not None:
                updates.append("concept_type=?")
                params.append(concept_type)
            if description is not None:
                updates.append("description=?")
                params.append(description)
            params.append(cid)
            cur.execute(
                f"UPDATE kb_concepts SET {', '.join(updates)} WHERE id=?",
                params,
            )
            return cid


def list_kb_concepts(kb_id: str, limit: int = 1000) -> list[dict]:
    """列出某 KB 下所有概念（按 mention_count 倒序）。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM kb_concepts WHERE kb_id=? ORDER BY mention_count DESC, id ASC LIMIT ?",
            (kb_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def list_kb_concepts_by_file(file_id: int) -> list[dict]:
    """列出引用了某文件的所有概念（source_file_ids_json 含 file_id）。"""
    with get_cursor() as cur:
        cur.execute("SELECT kb_id FROM document_meta WHERE file_id=?", (file_id,))
        row = cur.fetchone()
        if row is None:
            return []
        kb_id = row["kb_id"]
        cur.execute(
            "SELECT * FROM kb_concepts WHERE kb_id=? ORDER BY mention_count DESC, id ASC",
            (kb_id,),
        )
        all_concepts = [dict(r) for r in cur.fetchall()]
        # 过滤：source_file_ids_json 包含 file_id
        out: list[dict] = []
        for c in all_concepts:
            try:
                src_ids = json.loads(c.get("source_file_ids_json") or "[]")
            except Exception:
                src_ids = []
            if file_id in src_ids:
                out.append(c)
        return out


def find_concepts_in_text(kb_id: str, text: str, min_mention: int = 1) -> list[dict]:
    """在文本里找出已知的 KB 概念（用于 agentic 检索的概念扩展）。

    策略：
    - 拉出 KB 所有概念（SQL 预过滤：mention_count >= 阈值 + 名称长度 >= 2）
    - 对每个 concept_name 做大小写不敏感的子串匹配
    - 返回命中的概念列表（按 mention_count 倒序）

    Returns: [{"id", "concept_name", "concept_type", "source_file_ids": [int], "mention_count"}]
    """
    if not text:
        return []
    text_lower = text.lower()
    with get_cursor() as cur:
        # P2-20: SQL 预过滤（减少内存拉取量）
        # - mention_count >= 阈值（避免拉低质量概念）
        # - LENGTH(concept_name) >= 2（避免单字符概念噪声）
        cur.execute(
            "SELECT id, concept_name, concept_type, source_file_ids_json, mention_count "
            "FROM kb_concepts WHERE kb_id=? AND mention_count >= ? AND LENGTH(concept_name) >= 2 "
            "ORDER BY mention_count DESC, LENGTH(concept_name) DESC",
            (kb_id, min_mention),
        )
        rows = [dict(r) for r in cur.fetchall()]

    out: list[dict] = []
    for r in rows:
        if r["mention_count"] < min_mention:
            continue
        name = r["concept_name"] or ""
        # 概念名通常 >= 2 字符才有意义；避免单字符误匹配
        if len(name) < 2:
            continue
        # 大小写不敏感子串匹配
        if name.lower() in text_lower:
            try:
                file_ids = json.loads(r.get("source_file_ids_json") or "[]")
            except Exception:
                file_ids = []
            out.append({
                "id": r["id"],
                "concept_name": name,
                "concept_type": r["concept_type"],
                "source_file_ids": file_ids,
                "mention_count": r["mention_count"],
            })
    return out


def delete_kb_concept(concept_id: int) -> None:
    """删除单个概念 + 其关联。"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM concept_relations WHERE source_concept_id=? OR target_concept_id=?", (concept_id, concept_id))
        cur.execute("DELETE FROM kb_concepts WHERE id=?", (concept_id,))


def remove_file_from_concepts(file_id: int) -> int:
    """从所有概念的 source_file_ids_json 移除该 file_id（删文档时级联）。

    mention_count 不会自动减少（保留历史提及记录）。
    Returns: 受影响的概念行数
    """
    with get_cursor() as cur:
        return _remove_file_from_concepts_in_tx(cur, file_id)


def _remove_file_from_concepts_in_tx(cur: sqlite3.Cursor, file_id: int) -> int:
    """事务内版本：从所有概念的 source_file_ids_json 移除该 file_id。

    用 json_each 准确匹配（source_file_ids_json 是整数数组，不能用 LIKE）。
    """
    # 用 json_each 找出包含 file_id 的 concept id
    cur.execute(
        """
        SELECT DISTINCT kb_concepts.id
        FROM kb_concepts, json_each(kb_concepts.source_file_ids_json)
        WHERE json_each.value = ?
        """,
        (file_id,),
    )
    concept_ids = [r["id"] if isinstance(r, sqlite3.Row) else r[0] for r in cur.fetchall()]
    for cid in concept_ids:
        cur.execute("SELECT source_file_ids_json FROM kb_concepts WHERE id=?", (cid,))
        row = cur.fetchone()
        if row is None:
            continue
        try:
            src_ids = json.loads(row["source_file_ids_json"] or "[]")
        except Exception:
            src_ids = []
        if file_id in src_ids:
            src_ids.remove(file_id)
            cur.execute(
                "UPDATE kb_concepts SET source_file_ids_json=? WHERE id=?",
                (json.dumps(src_ids), cid),
            )
    return len(concept_ids)


# ===== knowledge_files CRUD =====


def upsert_file(
    relative_path: str,
    absolute_path: str,
    content_hash: str,
    file_size: int,
    file_type: str,
    source_package: str | None = None,
    kb_id: str = "default",
) -> int:
    """新增或更新文件记录（status=pending）。

    参数：
        kb_id: 目标知识库 ID（默认 'default'）

    冲突语义（v7+）：
        - 复合 UNIQUE(kb_id, relative_path)
        - 同一 KB 下同 relative_path：更新（重投喂）
        - 跨 KB 同 relative_path：分别独立存在（不再偷偷迁移 kb_id）
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowledge_files
                (relative_path, absolute_path, content_hash, file_size, file_type, source_package, kb_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(kb_id, relative_path) DO UPDATE SET
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
            (relative_path, absolute_path, content_hash, file_size, file_type, source_package, kb_id),
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
            (json.dumps(chunk_ids), len(chunk_ids), datetime.now(timezone.utc).isoformat(), file_id),
        )


def reset_file_to_pending(file_id: int) -> None:
    """重置文件为 pending 状态（清空 chunk_ids，用于重新入库）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_files
            SET status='pending', chunk_ids_json='[]', chunk_count=0, processed_at=NULL, error_message=NULL
            WHERE id=?
            """,
            (file_id,),
        )


def set_file_failed(file_id: int, error: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE knowledge_files SET status='failed', error_message=?, processed_at=? WHERE id=?",
            (error[:500], datetime.now(timezone.utc).isoformat(), file_id),
        )


def get_file_by_path(relative_path: str, kb_id: str | None = None) -> dict | None:
    """按路径查文件。

    v7+ 行为：
    - 传 kb_id：精确匹配 (kb_id, relative_path)
    - 不传 kb_id：返回任意 KB 下匹配 relative_path 的第一条（兼容旧调用）
      注意：跨 KB 同名场景下结果不确定，新代码应传 kb_id
    """
    with get_cursor() as cur:
        if kb_id is not None:
            cur.execute(
                "SELECT * FROM knowledge_files WHERE kb_id=? AND relative_path=?",
                (kb_id, relative_path),
            )
        else:
            cur.execute(
                "SELECT * FROM knowledge_files WHERE relative_path=? LIMIT 1",
                (relative_path,),
            )
        row = cur.fetchone()
        return dict(row) if row else None


def list_files(
    status: str | None = None,
    limit: int = 1000,
    kb_id: str | None = None,
) -> list[dict]:
    with get_cursor() as cur:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if kb_id is not None:
            clauses.append("kb_id=?")
            params.append(kb_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cur.execute(
            f"SELECT * FROM knowledge_files {where} ORDER BY id DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in cur.fetchall()]


def delete_file(relative_path: str, kb_id: str | None = None) -> dict | None:
    """删除文件记录，返回被删记录（包含 chunk_ids 用于从向量库清理）。

    v7+ 行为：
    - 传 kb_id：精确删 (kb_id, relative_path)
    - 不传 kb_id：删任意 KB 下匹配的第一条（兼容旧调用，跨 KB 同名下不确定）

    同时级联清理 document_meta（1:1 关系）。
    删除顺序：feedback_queue → document_meta → knowledge_files（满足 FK 约束）。
    """
    with get_cursor() as cur:
        if kb_id is not None:
            cur.execute(
                "SELECT * FROM knowledge_files WHERE kb_id=? AND relative_path=?",
                (kb_id, relative_path),
            )
        else:
            cur.execute(
                "SELECT * FROM knowledge_files WHERE relative_path=? LIMIT 1",
                (relative_path,),
            )
        row = cur.fetchone()
        if not row:
            return None
        file_id = row["id"]
        # 先删引用 knowledge_files.id 的表（FK 约束）
        cur.execute("DELETE FROM feedback_queue WHERE knowledge_file_id=?", (file_id,))
        cur.execute("DELETE FROM document_meta WHERE file_id=?", (file_id,))
        # 从所有概念的 source_file_ids_json 移除该 file_id（保留概念本身）
        try:
            _remove_file_from_concepts_in_tx(cur, file_id)
        except Exception as e:
            # 概念清理失败不致命（概念仍存在，只是 source_file_ids 没及时清理）
            import logging
            logging.getLogger(__name__).warning(
                f"删除文件 {file_id} 时清理 kb_concepts.source_file_ids 失败: {e}"
            )
        # 再删 knowledge_files 主表
        cur.execute("DELETE FROM knowledge_files WHERE id=?", (file_id,))
        return dict(row)


def list_files_in_kb(kb_id: str, status: str | None = None) -> list[dict]:
    """列出某 KB 下所有文件记录（用于删 KB 前收集 chunk_ids 等）。

    status 非空时按状态过滤（如 'failed' / 'done' / 'pending'）。
    """
    with get_cursor() as cur:
        if status:
            cur.execute(
                """
                SELECT id, kb_id, relative_path, absolute_path, content_hash, file_size, file_type,
                       source_package, status, chunk_ids_json, chunk_count,
                       processed_at, error_message, created_at
                FROM knowledge_files
                WHERE kb_id=? AND status=?
                ORDER BY id ASC
                """,
                (kb_id, status),
            )
        else:
            cur.execute(
                """
                SELECT id, kb_id, relative_path, absolute_path, content_hash, file_size, file_type,
                       source_package, status, chunk_ids_json, chunk_count,
                       processed_at, error_message, created_at
                FROM knowledge_files
                WHERE kb_id=?
                ORDER BY id ASC
                """,
                (kb_id,),
            )
        return [dict(r) for r in cur.fetchall()]


def get_stats(kb_id: str | None = None) -> dict[str, Any]:
    with get_cursor() as cur:
        where = "WHERE kb_id=?" if kb_id is not None else ""
        params: list[Any] = [kb_id] if kb_id is not None else []
        cur.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status='done' THEN chunk_count ELSE 0 END) AS total_chunks,
                SUM(CASE WHEN status='done' THEN file_size ELSE 0 END) AS done_size
            FROM knowledge_files
            {where}
            """,
            params,
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
            (decision, reviewer, note, datetime.now(timezone.utc).isoformat(), knowledge_file_id, fid),
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
            (key, v, datetime.now(timezone.utc).isoformat()),
        )


def delete_state(key: str) -> bool:
    """删除 app_state 中的某个 key。返回是否删除了行。"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM app_state WHERE key=?", (key,))
        return cur.rowcount > 0


# ===== sessions / turns CRUD =====
# 时间戳统一用 unix ms（前端 Date.now() 直接消费）


def create_session(
    session_id: str,
    title: str,
    created_at: int,
    updated_at: int,
    kb_scope: str | None = None,
    retrieval_mode: str = "ai",
) -> None:
    if retrieval_mode not in ("basic", "deep", "ai"):
        retrieval_mode = "ai"
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at, turn_count, kb_scope, retrieval_mode)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (session_id, title[:80] or "未命名会话", created_at, updated_at, kb_scope, retrieval_mode),
        )


def list_sessions(limit: int = 200) -> list[dict]:
    """列表（不含 turns，节省带宽）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, created_at, updated_at, turn_count, kb_scope, retrieval_mode
            FROM sessions
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_session(session_id: str) -> dict | None:
    """详情（含 turns）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, created_at, updated_at, turn_count, kb_scope, retrieval_mode
            FROM sessions WHERE id=?
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        session = dict(row)
        cur.execute(
            """
            SELECT id, order_idx, question, answer, sources_json, trace_json,
                   used_provider, liked, error, created_at, mode
            FROM turns
            WHERE session_id=?
            ORDER BY order_idx ASC
            """,
            (session_id,),
        )
        turns = []
        for r in cur.fetchall():
            t = dict(r)
            t["sources"] = json.loads(t.pop("sources_json") or "[]")
            t["trace"] = json.loads(t.pop("trace_json") or "[]")
            t["liked"] = bool(t["liked"])
            turns.append(t)
        session["turns"] = turns
        return session


def update_session(
    session_id: str,
    title: str | None | _Unset = _UNSET,
    updated_at: int | None | _Unset = _UNSET,
    kb_scope: str | None | _Unset = _UNSET,
    retrieval_mode: str | None | _Unset = _UNSET,
) -> bool:
    """更新会话元数据。返回是否找到。

    字段语义：
    - 省略（_UNSET）：不更新
    - None：清空为 NULL（仅对 kb_scope 有意义；title 会兜底为"未命名会话"）
    - 其他值：正常更新
    """
    fields = []
    params: list[Any] = []
    if not isinstance(title, _Unset):
        fields.append("title=?")
        # _UNSET 不更新；None 显式清空（兜底为"未命名会话"，因 NOT NULL）；
        # 全空白也兜底（避免 "   " 被当 truthy 保留）
        if title is None:
            params.append("未命名会话")
        else:
            stripped = title.strip()
            params.append(stripped[:80] or "未命名会话")
    if not isinstance(updated_at, _Unset):
        fields.append("updated_at=?")
        params.append(updated_at)
    if not isinstance(kb_scope, _Unset):
        fields.append("kb_scope=?")
        params.append(kb_scope)
    if not isinstance(retrieval_mode, _Unset):
        if retrieval_mode not in ("basic", "deep", "ai"):
            raise ValueError(f"非法 retrieval_mode: {retrieval_mode}")
        fields.append("retrieval_mode=?")
        params.append(retrieval_mode)
    if not fields:
        return False
    params.append(session_id)
    with get_cursor() as cur:
        cur.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id=?", params)
        return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    with get_cursor() as cur:
        # 先清空 ai_call_logs 里关联此 session 的引用（保留日志，不删）
        try:
            cur.execute(
                "UPDATE ai_call_logs SET session_id=NULL, turn_id=NULL WHERE session_id=?",
                (session_id,),
            )
        except Exception:
            pass  # 老 DB 没有 ai_call_logs 表，忽略
        cur.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return cur.rowcount > 0


def add_turn(
    session_id: str,
    turn_id: str,
    order_idx: int | None,
    question: str,
    answer: str | None,
    sources_json: str,
    trace_json: str,
    used_provider: str | None,
    liked: bool,
    error: str | None,
    created_at: int,
    mode: str = "ai",
) -> bool:
    """追加 turn。同时更新 session.turn_count 和 updated_at。
    返回是否成功（False 表示 session 不存在）。

    order_idx:
        - None（推荐）：自动用 COALESCE(MAX(order_idx), -1)+1，并发安全
        - 显式传值：保留调用方语义（导入场景需要连续 idx）
    mode: 消息形态 'basic' / 'deep'（检索结果列表）或 'ai'（LLM 回答）
    """
    if mode not in ("basic", "deep", "ai"):
        mode = "ai"
    with get_cursor() as cur:
        cur.execute("SELECT id FROM sessions WHERE id=?", (session_id,))
        if not cur.fetchone():
            return False

        if order_idx is None:
            cur.execute(
                "SELECT COALESCE(MAX(order_idx), -1) + 1 AS next_idx FROM turns WHERE session_id=?",
                (session_id,),
            )
            row = cur.fetchone()
            order_idx = int(row["next_idx"]) if row else 0

        cur.execute(
            """
            INSERT INTO turns
                (id, session_id, order_idx, question, answer, sources_json, trace_json,
                 used_provider, liked, error, created_at, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id, session_id, order_idx, question, answer,
                sources_json, trace_json, used_provider, 1 if liked else 0, error, created_at, mode,
            ),
        )
        cur.execute(
            "UPDATE sessions SET turn_count=turn_count+1, updated_at=? WHERE id=?",
            (created_at, session_id),
        )
        return True


def update_turn(
    session_id: str,
    turn_id: str,
    answer: str | None | _Unset = _UNSET,
    sources_json: str | None | _Unset = _UNSET,
    trace_json: str | None | _Unset = _UNSET,
    used_provider: str | None | _Unset = _UNSET,
    liked: bool | None | _Unset = _UNSET,
    error: str | None | _Unset = _UNSET,
    updated_at: int | None | _Unset = _UNSET,
) -> bool:
    """更新 turn 字段。同时刷新 session.updated_at（如果传了 updated_at）。

    字段语义：
    - 省略（_UNSET）：不更新
    - None：清空为 NULL（重试成功后清 error / 清 answer 等）
    - 其他值：正常更新
    """
    fields = []
    params: list[Any] = []
    if not isinstance(answer, _Unset):
        fields.append("answer=?")
        params.append(answer)
    if not isinstance(sources_json, _Unset):
        fields.append("sources_json=?")
        params.append(sources_json)
    if not isinstance(trace_json, _Unset):
        fields.append("trace_json=?")
        params.append(trace_json)
    if not isinstance(used_provider, _Unset):
        fields.append("used_provider=?")
        params.append(used_provider)
    if not isinstance(liked, _Unset):
        fields.append("liked=?")
        params.append(1 if liked else 0)
    if not isinstance(error, _Unset):
        fields.append("error=?")
        params.append(error)
    if not fields:
        return False
    params.extend([session_id, turn_id])
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE turns SET {', '.join(fields)} WHERE session_id=? AND id=?",
            params,
        )
        updated = cur.rowcount > 0
        if updated and not isinstance(updated_at, _Unset):
            cur.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (updated_at, session_id),
            )
        return updated


def delete_turn(session_id: str, turn_id: str) -> bool:
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM turns WHERE session_id=? AND id=?",
            (session_id, turn_id),
        )
        deleted = cur.rowcount > 0
        if deleted:
            cur.execute(
                "UPDATE sessions SET turn_count = MAX(0, turn_count - 1) WHERE id=?",
                (session_id,),
            )
        return deleted


def delete_all_turns(session_id: str) -> int:
    """清空某会话的所有 turns（切 KB 时调用，旧 turns 跟新 KB 不匹配）。

    Returns: 删除的 turns 数
    """
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM turns WHERE session_id=?",
            (session_id,),
        )
        deleted = cur.rowcount
        if deleted > 0:
            cur.execute(
                "UPDATE sessions SET turn_count=0 WHERE id=?",
                (session_id,),
            )
        return deleted


def get_turn(session_id: str, turn_id: str) -> dict | None:
    """读取单个 turn（按 session_id + turn_id）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, session_id, order_idx, question, answer, sources_json, trace_json,
                   used_provider, liked, error, created_at, mode
            FROM turns WHERE session_id=? AND id=?
            """,
            (session_id, turn_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


# ===== kbs CRUD =====


def create_kb(
    kb_id: str,
    name: str,
    collection_name: str,
    source: str = "user",
    description: str = "",
    embedding_model: str | None = None,
    embedding_dim: int | None = None,
    is_default: bool = False,
) -> None:
    """新建 KB。"""
    import time

    now_ms = int(time.time() * 1000)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO kbs (id, name, description, collection_name, source, embedding_model, embedding_dim, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (kb_id, name[:100] or "未命名知识库", description[:500] or None, collection_name, source, embedding_model, embedding_dim, 1 if is_default else 0, now_ms, now_ms),
        )


def list_kbs(limit: int = 100) -> list[dict]:
    """列表（不含 documents）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, description, collection_name, source, embedding_model, embedding_dim, is_default, created_at, updated_at
            FROM kbs
            ORDER BY is_default DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_kb(kb_id: str) -> dict | None:
    """详情（不含 documents）。"""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, description, collection_name, source, embedding_model, embedding_dim, is_default, created_at, updated_at
            FROM kbs
            WHERE id=?
            """,
            (kb_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_default_kb() -> dict | None:
    """获取默认 KB。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, name, description, collection_name, source, embedding_model, embedding_dim, is_default, created_at, updated_at FROM kbs WHERE is_default=1 LIMIT 1",
        )
        row = cur.fetchone()
        return dict(row) if row else None


def update_kb(
    kb_id: str,
    name: str | None | _Unset = _UNSET,
    description: str | None | _Unset = _UNSET,
    embedding_model: str | None | _Unset = _UNSET,
    embedding_dim: int | None | _Unset = _UNSET,
    is_default: bool | None | _Unset = _UNSET,
) -> bool:
    """更新 KB 元数据。返回是否找到。

    字段语义：
    - 省略（_UNSET）：不更新
    - None：清空为 NULL（如清空 description / embedding_model）
    - 其他值：正常更新
    """
    import time

    fields = []
    params: list[Any] = []
    if not isinstance(name, _Unset):
        fields.append("name=?")
        # _UNSET 不更新；None 显式清空（兜底为"未命名知识库"，因 NOT NULL）；
        # 全空白也兜底（避免用户传 "   " 当名字）
        if name is None:
            params.append("未命名知识库")
        else:
            stripped = name.strip()
            params.append(stripped[:100] or "未命名知识库")
    if not isinstance(description, _Unset):
        fields.append("description=?")
        params.append(description[:500] or None if description else None)
    if not isinstance(embedding_model, _Unset):
        fields.append("embedding_model=?")
        params.append(embedding_model)
    if not isinstance(embedding_dim, _Unset):
        fields.append("embedding_dim=?")
        params.append(embedding_dim)
    if not isinstance(is_default, _Unset):
        fields.append("is_default=?")
        params.append(1 if is_default else 0)
    if not fields:
        return False

    # 更新 updated_at
    fields.append("updated_at=?")
    params.append(int(time.time() * 1000))

    params.append(kb_id)
    with get_cursor() as cur:
        cur.execute(f"UPDATE kbs SET {', '.join(fields)} WHERE id=?", params)
        return cur.rowcount > 0


def update_kb_global_summary(
    kb_id: str, summary: str, model: str, tokens: int,
    snapshot: dict | None = None,
) -> bool:
    """更新 KB 全局摘要。返回是否找到 KB。

    snapshot：生成摘要时的文件快照 {file_id: content_hash}（JSON 存储），
    用于之后判断摘要是否过期（KB 文件集有变化即过期）。
    summary 为空（删除摘要）时快照一并清空。
    """
    import json
    import time
    now_ms = int(time.time() * 1000)
    snapshot_json = json.dumps(snapshot) if (snapshot and summary) else None
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE kbs SET
                global_summary=?, global_summary_model=?, global_summary_tokens=?,
                global_summary_created_at=?, global_summary_snapshot=?, updated_at=?
            WHERE id=?
            """,
            (summary, model, tokens, now_ms, snapshot_json, now_ms, kb_id),
        )
        return cur.rowcount > 0


def get_kb_global_summary(kb_id: str) -> dict | None:
    """读取 KB 全局摘要。返回 None 或 dict(summary, model, tokens, created_at, snapshot)。"""
    import json
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT global_summary, global_summary_model, global_summary_tokens,
                   global_summary_created_at, global_summary_snapshot
            FROM kbs WHERE id=?
            """,
            (kb_id,),
        )
        row = cur.fetchone()
        if row is None or not row["global_summary"]:
            return None
        snapshot = None
        if row["global_summary_snapshot"]:
            try:
                snapshot = json.loads(row["global_summary_snapshot"])
            except (ValueError, TypeError):
                snapshot = None
        return {
            "summary": row["global_summary"],
            "model": row["global_summary_model"],
            "tokens": row["global_summary_tokens"],
            "created_at": row["global_summary_created_at"],
            "snapshot": snapshot,
        }


def list_kb_file_hashes(kb_id: str) -> dict[str, str]:
    """KB 下全部已入库（done）文件的 {file_id: content_hash}，用于摘要过期比对。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, content_hash FROM knowledge_files WHERE kb_id=? AND status='done'",
            (kb_id,),
        )
        return {str(r["id"]): r["content_hash"] for r in cur.fetchall()}


def is_kb_summary_stale(kb_id: str) -> bool:
    """KB 全局摘要是否已过期（生成后 KB 文件集有新增/变更/删除）。

    无摘要 → False（不存在过期概念）。
    有摘要但无快照（升级前的旧摘要）→ False（视为未过期，下次重新生成会写入快照）。
    """
    data = get_kb_global_summary(kb_id)
    if data is None or not data.get("snapshot"):
        return False
    return data["snapshot"] != list_kb_file_hashes(kb_id)


def delete_kb_record(kb_id: str) -> bool:
    """删除 kbs 表中该 KB 的元数据记录（仅 kbs 表一行，不做级联清理）。

    警告：这个函数不做级联清理！调用方必须自行处理：
    - knowledge_files（document_meta, kb_concepts, concept_relations 已通过 FK CASCADE）
    - sessions.kb_scope（设为 NULL）
    - Chroma collection
    - BM25 索引中的 chunks
    - feed_folder/{kb_id}/ 子目录

    通常你应该调 routes_kbs.delete_kb 而不是这个函数。

    返回是否成功（False 表示 KB 不存在 / 是 builtin / 是默认 KB）。
    """
    with get_cursor() as cur:
        # 先检查是否是 builtin 或默认
        cur.execute("SELECT source, is_default FROM kbs WHERE id=?", (kb_id,))
        row = cur.fetchone()
        if not row:
            return False
        if row["source"] == "builtin" or row["is_default"]:
            # builtin 或默认 KB 不允许删除
            return False
        cur.execute("DELETE FROM kbs WHERE id=?", (kb_id,))
        return cur.rowcount > 0


# Deprecated alias（保留向后兼容；新代码请用 delete_kb_record）
def delete_kb(kb_id: str) -> bool:
    """[Deprecated] 请使用 delete_kb_record。"""
    return delete_kb_record(kb_id)


# ===== upload_tasks + upload_task_files CRUD =====
#
# 数据模型：
#   upload_tasks:    一个批量上传任务（一行）
#   upload_task_files: 任务里的每个文件（子表，CASCADE 删除）
#
# 任务状态机：
#   running → completed（全部成功）
#   running → cancelled（用户取消未完成）
#   running → paused（进程崩溃，启动时检测改 paused）
#   paused/failed → running（用户 retry）


def create_upload_task(
    task_id: str,
    kb_id: str,
    skip_mode: str,
    auto_ingest: bool,
    files: list[dict[str, Any]],
    *,
    upload_complete: bool = True,
) -> None:
    """创建批量上传任务 + 所有子文件记录（原子）。

    files: [{ relative_path, absolute_path, file_size }, ...]
    upload_complete: False 表示分批上传还没传完（status='uploading'），等 finalize。
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    status = "running" if upload_complete else "uploading"
    with get_cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(
                """INSERT INTO upload_tasks
                   (id, kb_id, skip_mode, auto_ingest, total, done, skipped, failed,
                    status, upload_complete, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)""",
                (task_id, kb_id, skip_mode, 1 if auto_ingest else 0, len(files),
                 status, 1 if upload_complete else 0, now_ms, now_ms),
            )
            for f in files:
                cur.execute(
                    """INSERT INTO upload_task_files
                       (task_id, relative_path, absolute_path, file_size, status)
                       VALUES (?, ?, ?, ?, 'queued')""",
                    (task_id, f["relative_path"], str(f["absolute_path"]), f["file_size"]),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def append_upload_task_files(task_id: str, files: list[dict[str, Any]]) -> int:
    """向已有任务追加文件记录（分批上传的后续批次），total 同步增加。

    返回实际追加数（同 relative_path 已存在的跳过）。
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    appended = 0
    with get_cursor() as cur:
        cur.execute("BEGIN")
        try:
            existing = {
                r[0] for r in cur.execute(
                    "SELECT relative_path FROM upload_task_files WHERE task_id=?",
                    (task_id,),
                ).fetchall()
            }
            for f in files:
                if f["relative_path"] in existing:
                    continue
                cur.execute(
                    """INSERT INTO upload_task_files
                       (task_id, relative_path, absolute_path, file_size, status)
                       VALUES (?, ?, ?, ?, 'queued')""",
                    (task_id, f["relative_path"], str(f["absolute_path"]), f["file_size"]),
                )
                existing.add(f["relative_path"])
                appended += 1
            if appended:
                cur.execute(
                    "UPDATE upload_tasks SET total = total + ?, updated_at=? WHERE id=?",
                    (appended, now_ms, task_id),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    return appended


def get_upload_task(task_id: str) -> dict[str, Any] | None:
    """返回任务详情（不含 files）。"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM upload_tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_active_upload_tasks() -> list[dict[str, Any]]:
    """返回所有未完成任务（running / paused / uploading）。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM upload_tasks WHERE status IN ('running', 'paused', 'uploading') "
            "ORDER BY created_at DESC"
        )
        return [dict(r) for r in cur.fetchall()]


def list_upload_task_files(
    task_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    """返回任务的子文件列表，可按 status 过滤。"""
    with get_cursor() as cur:
        if status:
            cur.execute(
                "SELECT * FROM upload_task_files WHERE task_id=? AND status=? ORDER BY id",
                (task_id, status),
            )
        else:
            cur.execute(
                "SELECT * FROM upload_task_files WHERE task_id=? ORDER BY id",
                (task_id,),
            )
        return [dict(r) for r in cur.fetchall()]


def get_upload_task_file(file_id: int) -> dict[str, Any] | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM upload_task_files WHERE id=?", (file_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_upload_task_file_status(
    file_id: int,
    status: str,
    *,
    skip_reason: str | None = _UNSET,
    error_message: str | None = _UNSET,
    started_at: int | None = _UNSET,
    finished_at: int | None = _UNSET,
) -> None:
    """更新单文件状态。started_at/finished_at 用 _UNSET 表示不更新。"""
    fields = ["status=?"]
    params: list[Any] = [status]
    if not isinstance(skip_reason, _Unset):
        fields.append("skip_reason=?")
        params.append(skip_reason)
    if not isinstance(error_message, _Unset):
        fields.append("error_message=?")
        params.append(error_message)
    if not isinstance(started_at, _Unset):
        fields.append("started_at=?")
        params.append(started_at)
    if not isinstance(finished_at, _Unset):
        fields.append("finished_at=?")
        params.append(finished_at)
    params.append(file_id)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE upload_task_files SET {', '.join(fields)} WHERE id=?",
            params,
        )


def update_upload_task_progress(
    task_id: str,
    *,
    done: int | None = _UNSET,
    skipped: int | None = _UNSET,
    failed: int | None = _UNSET,
    status: str | None = _UNSET,
    upload_complete: bool | None = _UNSET,
    current_file_path: str | None = _UNSET,
    current_stage: str | None = _UNSET,
    finished_at: int | None = _UNSET,
) -> None:
    """更新任务级进度。所有字段用 _UNSET 表示不更新，None 表示清空。"""
    fields: list[str] = []
    params: list[Any] = []
    for key, val in [
        ("done", done),
        ("skipped", skipped),
        ("failed", failed),
        ("status", status),
        ("upload_complete", upload_complete if isinstance(upload_complete, _Unset) else (1 if upload_complete else 0)),
        ("current_file_path", current_file_path),
        ("current_stage", current_stage),
        ("finished_at", finished_at),
    ]:
        if not isinstance(val, _Unset):
            fields.append(f"{key}=?")
            params.append(val)
    if not fields:
        return
    fields.append("updated_at=?")
    params.append(int(datetime.now(timezone.utc).timestamp() * 1000))
    params.append(task_id)
    with get_cursor() as cur:
        cur.execute(
            f"UPDATE upload_tasks SET {', '.join(fields)} WHERE id=?",
            params,
        )


def delete_upload_task(task_id: str) -> bool:
    """删除任务记录（含子文件，CASCADE）。已 ingest 的 knowledge_files 保留。"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM upload_tasks WHERE id=?", (task_id,))
        return cur.rowcount > 0


def delete_upload_task_file(file_id: int) -> bool:
    """删除 upload_task_files 子表的指定行（不影响 knowledge_files 主表）。"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM upload_task_files WHERE id=?", (file_id,))
        return cur.rowcount > 0


def reset_file_to_queued(file_id: int) -> None:
    """重置 failed 文件为 queued（用于 retry）。"""
    with get_cursor() as cur:
        cur.execute(
            """UPDATE upload_task_files
               SET status='queued', error_message=NULL,
                   started_at=NULL, finished_at=NULL
               WHERE id=?""",
            (file_id,),
        )


def list_kb_file_paths(kb_id: str) -> list[str]:
    """轻量查询：返回 KB 内所有 relative_path（用于前端预检同名冲突）。"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT relative_path FROM knowledge_files WHERE kb_id=? ORDER BY relative_path",
            (kb_id,),
        )
        return [r["relative_path"] for r in cur.fetchall()]


# ===== ai_call_logs CRUD =====


def insert_ai_call_log(
    *,
    provider: str,
    model: str | None,
    scene: str,
    messages: list[dict],
    response_text: str | None = None,
    system_prompt: str | None = None,
    duration_ms: int | None = None,
    success: bool = True,
    error_message: str | None = None,
    token_input: int | None = None,
    token_output: int | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    kb_id: str | None = None,
) -> int:
    """记录一次 AI 调用。返回插入的 id。

    messages 直接传 list[dict]，内部 json 序列化。
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO ai_call_logs
               (provider, model, scene, session_id, turn_id, kb_id,
                system_prompt, messages_json, response_text,
                duration_ms, success, error_message,
                token_input, token_output, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider,
                model,
                scene,
                session_id,
                turn_id,
                kb_id,
                system_prompt,
                json.dumps(messages, ensure_ascii=False),
                response_text,
                duration_ms,
                1 if success else 0,
                error_message,
                token_input,
                token_output,
                now_ms,
            ),
        )
        return cur.lastrowid or 0


def get_ai_call_log(log_id: int) -> dict[str, Any] | None:
    """返回单条日志详情（含完整 messages_json / response_text）。"""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM ai_call_logs WHERE id=?", (log_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        # 反序列化 messages_json
        try:
            d["messages"] = json.loads(d.pop("messages_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["messages"] = []
            d["messages_json"] = d.pop("messages_json", "[]")
        return d


def list_ai_call_logs(
    *,
    provider: str | None = None,
    scene: str | None = None,
    success: bool | None = None,
    session_id: str | None = None,
    kb_id: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """返回 (日志列表不含 messages_json/response_text 大字段, 总数)。

    列表用简略视图（不含大字段），详情用 get_ai_call_log。
    """
    where_parts: list[str] = []
    params: list[Any] = []
    if provider:
        where_parts.append("provider=?")
        params.append(provider)
    if scene:
        where_parts.append("scene=?")
        params.append(scene)
    if success is not None:
        where_parts.append("success=?")
        params.append(1 if success else 0)
    if session_id:
        where_parts.append("session_id=?")
        params.append(session_id)
    if kb_id:
        where_parts.append("kb_id=?")
        params.append(kb_id)
    if start_ts is not None:
        where_parts.append("created_at>=?")
        params.append(start_ts)
    if end_ts is not None:
        where_parts.append("created_at<=?")
        params.append(end_ts)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with get_cursor() as cur:
        # 总数
        cur.execute(f"SELECT COUNT(*) FROM ai_call_logs {where_sql}", params)
        total = int(cur.fetchone()[0])

        # 列表（不含大字段，避免单次响应过大）
        cur.execute(
            f"""SELECT id, provider, model, scene, session_id, turn_id, kb_id,
                      duration_ms, success, error_message,
                      token_input, token_output, created_at,
                      LENGTH(messages_json) AS messages_size,
                      LENGTH(response_text) AS response_size
               FROM ai_call_logs {where_sql}
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )
        items = [dict(r) for r in cur.fetchall()]

    return items, total


def delete_ai_call_log(log_id: int) -> bool:
    with get_cursor() as cur:
        cur.execute("DELETE FROM ai_call_logs WHERE id=?", (log_id,))
        return cur.rowcount > 0


def delete_ai_call_logs_batch(
    *,
    before_ts: int | None = None,
    provider: str | None = None,
    scene: str | None = None,
) -> int:
    """批量删除。返回删除的行数。

    - before_ts: 删除 created_at < before_ts 的（用于按时间清理）
    - provider: 删除指定 provider 的
    - scene: 删除指定 scene 的
    """
    where_parts: list[str] = []
    params: list[Any] = []
    if before_ts is not None:
        where_parts.append("created_at<?")
        params.append(before_ts)
    if provider:
        where_parts.append("provider=?")
        params.append(provider)
    if scene:
        where_parts.append("scene=?")
        params.append(scene)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with get_cursor() as cur:
        cur.execute(f"DELETE FROM ai_call_logs {where_sql}", params)
        return cur.rowcount


def clear_session_ai_call_logs(session_id: str) -> int:
    """删除会话相关的所有 AI 日志（session/turn 删除时调用）。

    实际行为：把 session_id/turn_id 设为 NULL（保留审计日志，不关联会话）。
    返回更新的行数。
    """
    with get_cursor() as cur:
        cur.execute(
            """UPDATE ai_call_logs SET session_id=NULL, turn_id=NULL
               WHERE session_id=?""",
            (session_id,),
        )
        return cur.rowcount


def get_ai_call_log_stats(
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    """统计：按 provider / scene 聚合，含总调用数 / 成功率 / 平均耗时。

    返回 {
        total: int,
        success: int,
        failed: int,
        by_provider: [{provider, total, success, failed, avg_duration_ms}],
        by_scene: [{scene, total, success, failed, avg_duration_ms}],
        by_day: [{date, total, success, failed}],  # 按天聚合（最近 30 天）
    }
    """
    where_parts: list[str] = []
    params: list[Any] = []
    if start_ts is not None:
        where_parts.append("created_at>=?")
        params.append(start_ts)
    if end_ts is not None:
        where_parts.append("created_at<=?")
        params.append(end_ts)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with get_cursor() as cur:
        # 总体
        cur.execute(
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed,
                AVG(duration_ms) AS avg_duration_ms
               FROM ai_call_logs {where_sql}""",
            params,
        )
        overall = dict(cur.fetchone())

        # 按 provider
        cur.execute(
            f"""SELECT provider,
                COUNT(*) AS total,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed,
                AVG(duration_ms) AS avg_duration_ms
               FROM ai_call_logs {where_sql}
               GROUP BY provider
               ORDER BY total DESC""",
            params,
        )
        by_provider = [dict(r) for r in cur.fetchall()]

        # 按 scene
        cur.execute(
            f"""SELECT scene,
                COUNT(*) AS total,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed,
                AVG(duration_ms) AS avg_duration_ms
               FROM ai_call_logs {where_sql}
               GROUP BY scene
               ORDER BY total DESC""",
            params,
        )
        by_scene = [dict(r) for r in cur.fetchall()]

        # 按天（最近 30 天）
        cur.execute(
            f"""SELECT
                DATE(created_at/1000, 'unixepoch', 'localtime') AS date,
                COUNT(*) AS total,
                SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed
               FROM ai_call_logs {where_sql}
               GROUP BY date
               ORDER BY date DESC
               LIMIT 30""",
            params,
        )
        by_day = [dict(r) for r in cur.fetchall()]

    return {
        "total": overall.get("total") or 0,
        "success": overall.get("success") or 0,
        "failed": overall.get("failed") or 0,
        "avg_duration_ms": int(overall.get("avg_duration_ms") or 0),
        "by_provider": by_provider,
        "by_scene": by_scene,
        "by_day": by_day,
    }


def cleanup_expired_ai_call_logs(retention_days: int = 30) -> int:
    """清理超过保留期的日志。返回清理的行数。"""
    threshold_ms = int((datetime.now(timezone.utc).timestamp() - retention_days * 86400) * 1000)
    return delete_ai_call_logs_batch(before_ts=threshold_ms)
