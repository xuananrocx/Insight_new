"""Read-only source preflight for complete and explicitly partial KB packs."""
import hashlib
import json
from pathlib import Path

from src.db import metadata_db as db
from src.knowledge.reading_index import fingerprint
from src.knowledge.pack_validation import portable_path


def inspect_sources(kb_id):
    files = db.list_files(kb_id=kb_id, limit=100000)
    available, excluded, snapshot = [], [], []
    for file in files:
        reason, actual = None, None
        path = Path(file['absolute_path'])
        try:
            portable_path(file['relative_path'])
            if file.get('status') != 'done':
                reason = '尚未完成入库'
            elif not path.is_file():
                reason = '原文件不存在或路径已失效'
            else:
                actual = fingerprint(path)
                if file.get('content_hash') and actual != file['content_hash']:
                    reason = '原文件已变化，需要重新入库'
        except (OSError, ValueError):
            reason = '原文件无法读取或路径不合法'
        snapshot.append([dict(file), actual, reason])
        if reason:
            excluded.append(dict(file_id=file['id'], path=file['relative_path'], reason=reason))
        else:
            available.append(file)
    token = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return available, dict(total=len(files), available=len(available), excluded=excluded, token=token)
