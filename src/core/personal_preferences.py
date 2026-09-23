"""A private Markdown preference file per account; never written by the model."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import uuid

from src.core import accounts
from src.core.config import USER_DATA_DIR

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_prefix = '<!-- insight-preferences: '


def _path():
    current = accounts.identity.get()
    if accounts.enabled and not current:
        from fastapi import HTTPException
        raise HTTPException(401, '请先登录')
    # The caller cannot supply a user ID or filesystem path.
    owner = current['id'] if current else 'local'
    key = hashlib.sha256(owner.encode()).hexdigest()
    return USER_DATA_DIR / 'preferences' / key / 'preferences.md'


def read():
    path = _path()
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {'content': '', 'enabled': True, 'version': ''}
    header, content = text.split('\n', 1)
    if not header.startswith(_prefix) or not header.endswith(' -->'):
        raise ValueError('个人偏好文件头损坏')
    meta = json.loads(header[len(_prefix):-4])
    if type(meta.get('enabled')) is not bool or len(content) > 4000:
        raise ValueError('个人偏好文件格式错误')
    return {'content': content, 'enabled': meta['enabled'], 'version': meta['version']}


def save(content: str, enabled: bool, version: str):
    from fastapi import HTTPException
    if len(content) > 4000:
        raise HTTPException(422, '个人偏好最多 4000 字符')
    with _lock:
        previous = read()
        if previous['version'] != version:
            raise HTTPException(409, '个人偏好已在其他页面修改，请重新加载后保存')
        result = {'content': content, 'enabled': enabled, 'version': uuid.uuid4().hex}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.preferences-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(_prefix + json.dumps({k: result[k] for k in ('enabled', 'version')}) + ' -->\n' + content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if accounts.enabled:
        if previous['content'] != content:
            accounts.audit('personal_preferences_saved')
        if previous['enabled'] != enabled:
            accounts.audit('personal_preferences_enabled' if enabled else 'personal_preferences_disabled')
    logger.info('个人偏好保存完成 user=%s enabled=%s chars=%s version=%s',
                (accounts.identity.get() or {}).get('id', 'local'), enabled, len(content), result['version'])
    return result
