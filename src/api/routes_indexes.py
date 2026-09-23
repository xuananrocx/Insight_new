from fastapi import APIRouter
from pydantic import BaseModel, Field
from src.core import accounts, permissions
from src.db import metadata_db as db
from src.knowledge import index_tasks, reading_index

router = APIRouter(prefix='/api/v1/kbs/{kb_id}/indexes',tags=['knowledge'])

class BuildRequest(BaseModel):
    file_ids: list[int] | None = Field(None,max_length=10000)
    force: bool = False

@router.get('')
def status(kb_id:str):
    permissions.require_resource('kb',kb_id,'query')
    with db.get_cursor() as cur:
        ids = [r[0] for r in cur.execute('SELECT id FROM index_tasks WHERE kb_id=? ORDER BY created_at DESC,rowid DESC LIMIT 5',(kb_id,))]
    return {'files':reading_index.statuses(kb_id),'tasks':[index_tasks.get(t,kb_id) for t in ids]}

@router.post('/rebuild')
def rebuild(kb_id:str,body:BuildRequest):
    permissions.require_resource('kb',kb_id,'manage')
    result=index_tasks.start(kb_id,body.file_ids,body.force)
    accounts.audit('kb_index_rebuild',kb_id)
    return result

@router.post('/tasks/{task_id}/cancel')
def cancel(kb_id:str,task_id:str):
    permissions.require_resource('kb',kb_id,'manage')
    index_tasks.get(task_id,kb_id)
    with db.get_cursor() as cur:
        cur.execute("UPDATE index_tasks SET cancel_requested=1,state='cancelling' WHERE id=? AND state IN ('queued','running')",(task_id,))
    accounts.audit('kb_index_cancel',kb_id)
    return index_tasks.get(task_id,kb_id)
