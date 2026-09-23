"""User-owned, single-level conversation groups."""
import uuid
from fastapi import HTTPException
from src.core import accounts
from src.db import metadata_db as db


def owner():
    return (accounts.identity.get() or {}).get("id", "__local__")


def require_group(cur, group_id):
    if group_id is not None and not cur.execute(
        "SELECT 1 FROM session_groups WHERE id=? AND owner_id=?", (group_id, owner())).fetchone():
        raise HTTPException(404, "会话分组不存在")


def list_groups():
    with db.get_cursor() as cur:
        return [dict(row) for row in cur.execute(
            "SELECT id,name,position FROM session_groups WHERE owner_id=? ORDER BY position,id", (owner(),))]


def create(name):
    name = name.strip()
    if not name or len(name) > 80:
        raise HTTPException(400, "分组名称须为1至80字")
    with db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        if cur.execute("SELECT 1 FROM session_groups WHERE owner_id=? AND name=?", (owner(), name)).fetchone():
            raise HTTPException(409, "已存在同名分组")
        position = cur.execute("SELECT COALESCE(MAX(position),-1)+1 FROM session_groups WHERE owner_id=?", (owner(),)).fetchone()[0]
        gid = uuid.uuid4().hex
        cur.execute("INSERT INTO session_groups VALUES (?,?,?,?)", (gid, owner(), name, position))
        cur.connection.commit()
    return {"id": gid, "name": name, "position": position}


def change(gid, name=None, direction=None, delete=False):
    with db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        require_group(cur, gid)
        if delete:
            cur.execute("UPDATE sessions SET group_id=NULL WHERE group_id=?", (gid,))
            cur.execute("DELETE FROM session_groups WHERE id=?", (gid,))
            cur.connection.commit()
            return
        if name is not None:
            name = name.strip()
            if not name or len(name) > 80:
                raise HTTPException(400, "分组名称须为1至80字")
            if cur.execute("SELECT 1 FROM session_groups WHERE owner_id=? AND name=? AND id<>?", (owner(), name, gid)).fetchone():
                raise HTTPException(409, "已存在同名分组")
            cur.execute("UPDATE session_groups SET name=? WHERE id=?", (name, gid))
        if direction:
            ids = [r[0] for r in cur.execute("SELECT id FROM session_groups WHERE owner_id=? ORDER BY position,id", (owner(),))]
            i = ids.index(gid)
            j = i + (-1 if direction == "up" else 1)
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                cur.executemany("UPDATE session_groups SET position=? WHERE id=?", enumerate(ids))

        cur.connection.commit()
