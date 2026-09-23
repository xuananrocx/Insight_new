"""Names are scoped to a private owner or the team's shared namespace."""
from fastapi import HTTPException
from src.core import accounts


def check(cur, name, kb_id=None, scope=None, owner_id=None):
    name = name.strip()
    if not name:
        raise HTTPException(400, "知识库名称不能为空")
    if not accounts.identity.get():
        return name
    previous = cur.execute("SELECT name FROM kbs WHERE id=?", (kb_id,)).fetchone() if kb_id else None
    if previous and previous[0] == name and scope is None and owner_id is None:
        return name  # Preserve existing duplicate names on unrelated edits.
    own = cur.execute("SELECT owner_id FROM auth_objects WHERE kind='kb' AND object_id=?", (kb_id,)).fetchone() if kb_id else None
    policy = cur.execute("SELECT scope FROM permission_kbs WHERE kb_id=?", (kb_id,)).fetchone() if kb_id else None
    scope = scope or (policy[0] if policy else "private")
    owner_id = owner_id or (own[0] if own else accounts.identity.get()["id"])
    rows = cur.execute("""SELECT k.id,k.name,o.owner_id,COALESCE(p.scope,'private') AS scope
        FROM kbs k LEFT JOIN auth_objects o ON o.kind='kb' AND o.object_id=k.id
        LEFT JOIN permission_kbs p ON p.kb_id=k.id""").fetchall()
    for row in rows:
        if row[0] != kb_id and row[3] == scope and (scope == "team" or row[2] == owner_id) and row[1].strip().casefold() == name.casefold():
            raise HTTPException(409, "团队内已存在同名知识库" if scope == "team" else "该用户已存在同名私有知识库")
    return name


def attribution(kb_id):
    if not accounts.identity.get():
        return {}
    rows = accounts.rows("""SELECT u.id,u.username FROM auth_objects o JOIN auth_users u ON u.id=o.owner_id
        WHERE o.kind='kb' AND o.object_id=?""", (kb_id,))
    return {"owner_id": rows[0]["id"], "owner_username": rows[0]["username"]} if rows else {}
