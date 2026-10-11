"""Changing knowledge scope must never discard existing conversation turns."""
import uuid

import pytest
from fastapi import HTTPException
from src.db import metadata_db as db
from src.api import routes_sessions as routes


def test_empty_session_switch_and_populated_session_guard():
    db.init_db()
    sid = 'kb-switch-' + uuid.uuid4().hex
    db.create_session(sid, 'original', 1, 1, kb_scope='kb-a')
    try:
        routes.update_session(sid, routes.UpdateSessionRequest(kb_scope='kb-b'))
        assert db.get_session(sid)['kb_scope'] == 'kb-b'
        routes.add_turn(sid, routes.AddTurnRequest(
            id='turn', question='keep question', answer='keep answer', created_at=2))
        for scope in ('kb-a', None):
            with pytest.raises(HTTPException) as error:
                routes.update_session(sid, routes.UpdateSessionRequest(kb_scope=scope, title='changed'))
            assert error.value.status_code == 409
            saved = db.get_session(sid)
            assert saved['kb_scope'] == 'kb-b'
            assert saved['title'] == 'original'
            assert saved['turns'][0]['answer'] == 'keep answer'
        routes.update_session(sid, routes.UpdateSessionRequest(kb_scope='kb-b', title='renamed'))
        assert db.get_session(sid)['title'] == 'renamed'
        assert db.get_session(sid)['turn_count'] == 1
    finally:
        db.delete_session(sid)
