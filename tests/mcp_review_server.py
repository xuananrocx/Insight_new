"""Temporary local review fixture; never opens the real user data directory.

Run: .venv/bin/python -m tests.mcp_review_server /tmp/mcp-review-info.json
The info file contains a short-lived test token. It is deleted on exit.
"""
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    info = Path(sys.argv[1])
    if info.exists():
        raise SystemExit('Choose a new, unused info path')
    with tempfile.TemporaryDirectory(prefix='insight-mcp-review-') as folder:
        os.environ['AMD_DATA_DIR'] = folder
        (Path(folder)/'data').mkdir()
        (Path(folder)/'logs').mkdir()
        from src.db import metadata_db as db
        from src.core import accounts as a
        from src.knowledge import reading_index as ri
        from src.mcp import store
        db.init_db()
        a.init_auth()
        a.bootstrap('mcp-review', 'temporary-review-password', (Path(folder)/'setup-token.txt').read_text().strip())
        uid = a.rows('SELECT id FROM auth_users WHERE username=?', ('mcp-review',))[0]['id']
        identity = a.identity.set(a.rows('SELECT * FROM auth_users WHERE id=?', (uid,))[0])
        kid = 'mcp_review_knowledge'
        db.create_kb(kid, 'MCP 验证知识库', '隔离测试资料：接入、查找与文档阅读')
        a.claim('kb', kid)
        path = Path(folder)/'guide.h'
        path.write_text('struct Quote {\n'+''.join(f' int field_{i}; // knowledge test\n' for i in range(160))+' int visible_levels;\n};\n')
        fid = db.upsert_file(path.name, str(path), ri.fingerprint(path), path.stat().st_size, 'h', kb_id=kid)
        db.set_file_processed(fid, [])
        file = db.get_file_by_path(path.name, kid)
        builder = ri.build_file(file)
        with db.get_cursor() as cur:
            builder.publish(cur, fid, file['content_hash'])
        store.save_config(store.Settings(enabled=True, public_url='http://127.0.0.1:18001/mcp'))
        result = store.create_token('SDK 集成验证', [kid], 1)
        info.write_text(json.dumps({'token': result['token'], 'kb_id': kid, 'document_id': fid}))
        info.chmod(0o600)
        a.identity.reset(identity)
        from src.api.main import app
        import uvicorn
        try:
            uvicorn.run(app, host='127.0.0.1', port=18000, lifespan='off', access_log=False)
        finally:
            info.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
