"""metadata_db 关键路径测试。

重点覆盖 _UNSET 哨兵语义（第一批 P0-1 修复）+ v6→v7 schema 迁移幂等性。
"""
import pytest

from src.db import metadata_db


# ===== _UNSET 哨兵语义 =====


def test_unset_is_singleton():
    """_UNSET 单例：两次获取应指向同一对象。"""
    from src.db.metadata_db import _UNSET
    assert _UNSET is _UNSET
    assert isinstance(_UNSET, type(_UNSET))


def test_update_session_title_unset_keeps_value():
    """_UNSET 默认：不传字段不更新。"""
    # 准备
    metadata_db.init_db()
    sid = "test-sid-1"
    metadata_db.create_session(sid, "原标题", 1000, 1000)
    try:
        # 不传任何字段
        ok = metadata_db.update_session(sid)
        assert ok is False  # 没字段更新返回 False
        # title 应不变
        s = metadata_db.get_session(sid)
        assert s["title"] == "原标题"
    finally:
        metadata_db.delete_session(sid)


def test_update_session_title_none_falls_back_to_default():
    """显式传 None 清空 title（NOT NULL 约束，兜底为「未命名会话」）。"""
    metadata_db.init_db()
    sid = "test-sid-2"
    metadata_db.create_session(sid, "原标题", 1000, 1000)
    try:
        ok = metadata_db.update_session(sid, title=None)
        assert ok is True
        s = metadata_db.get_session(sid)
        assert s["title"] == "未命名会话"
    finally:
        metadata_db.delete_session(sid)


def test_update_session_title_whitespace_falls_back_to_default():
    """全空白 title 兜底为「未命名会话」（防 "   " 被当 truthy 保留）。"""
    metadata_db.init_db()
    sid = "test-sid-3"
    metadata_db.create_session(sid, "原标题", 1000, 1000)
    try:
        ok = metadata_db.update_session(sid, title="   ")
        assert ok is True
        s = metadata_db.get_session(sid)
        assert s["title"] == "未命名会话"
    finally:
        metadata_db.delete_session(sid)


def test_update_turn_error_can_be_cleared():
    """update_turn 用 None 显式清空 error 字段。"""
    metadata_db.init_db()
    sid = "test-sid-4"
    tid = "test-tid-4"
    metadata_db.create_session(sid, "T", 1000, 1000)
    try:
        metadata_db.add_turn(sid, tid, None, "Q?", "A", "[]", "[]", None, False, "err msg", 1000)
        # 清空 error
        ok = metadata_db.update_turn(sid, tid, error=None)
        assert ok is True
        t = metadata_db.get_turn(sid, tid)
        assert t["error"] is None
    finally:
        metadata_db.delete_session(sid)


# ===== add_turn order_idx 自动算（P0-9 修复）=====


def test_add_turn_auto_order_idx():
    """add_turn(order_idx=None) 自动用 MAX+1（并发安全）。"""
    metadata_db.init_db()
    sid = "test-sid-5"
    metadata_db.create_session(sid, "T", 1000, 1000)
    try:
        metadata_db.add_turn(sid, "tid-1", None, "Q1", "A1", "[]", "[]", None, False, None, 1000)
        metadata_db.add_turn(sid, "tid-2", None, "Q2", "A2", "[]", "[]", None, False, None, 1000)
        t1 = metadata_db.get_turn(sid, "tid-1")
        t2 = metadata_db.get_turn(sid, "tid-2")
        assert t1["order_idx"] == 0
        assert t2["order_idx"] == 1
    finally:
        metadata_db.delete_session(sid)


# ===== upsert_file 跨 KB 同 rel_path 不互相覆盖（v7 复合 UNIQUE）=====


def test_upsert_file_cross_kb_no_overwrite():
    """KB-A 和 KB-B 同 rel_path 应分别独立存在（不会偷偷迁移 kb_id）。"""
    metadata_db.init_db()
    # KB-A / KB-B 必须存在（FK 约束）
    try:
        metadata_db.create_kb("test-kb-a", "KB-A", "kb_a_col", source="user")
        metadata_db.create_kb("test-kb-b", "KB-B", "kb_b_col", source="user")
        id_a = metadata_db.upsert_file("test.md", "/a/test.md", "hash_a", 100, "md", kb_id="test-kb-a")
        id_b = metadata_db.upsert_file("test.md", "/b/test.md", "hash_b", 200, "md", kb_id="test-kb-b")
        assert id_a != id_b
        fa = metadata_db.get_file_by_path("test.md", kb_id="test-kb-a")
        fb = metadata_db.get_file_by_path("test.md", kb_id="test-kb-b")
        assert fa["content_hash"] == "hash_a"
        assert fb["content_hash"] == "hash_b"
    finally:
        metadata_db.delete_file("test.md", kb_id="test-kb-a")
        metadata_db.delete_file("test.md", kb_id="test-kb-b")
        metadata_db.delete_kb_record("test-kb-a")
        metadata_db.delete_kb_record("test-kb-b")


# ===== schema 迁移幂等性 =====


def test_init_db_is_idempotent():
    """连续调 init_db 两次不应出错，schema_version 保持一致。"""
    metadata_db.init_db()
    metadata_db.init_db()  # 二次
    # 不应抛错；schema_version 等于 SCHEMA_VERSION
    import sqlite3
    from src.core.config import settings
    db_path = settings.get_path("metadata_db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA user_version")
    v = cur.fetchone()[0]
    conn.close()
    assert v == metadata_db.SCHEMA_VERSION
