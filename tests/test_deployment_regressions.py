import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from src.knowledge.pack_validation import inspect_pack
from src.knowledge import kb_pack
from src.core import llm_client, config


def pack(missing=False, duplicate=False):
    body = b'original document'
    manifest = {'kb': {'embedding_dim': 2}, 'files': [{
        'relative_path': 'folder\\a.txt', 'file_size': len(body),
        'content_hash': hashlib.sha256(body).hexdigest(), 'chunk_ids': ['a', 'b']}]}
    chunks = [{'id': cid, 'text': cid, 'vector': [.1, .2], 'metadata': {
        'file_id': 7, 'content_hash': manifest['files'][0]['content_hash'],
        'chunk_index': 0, 'section_index': section}} for cid, section in [('a', 0), ('b', 1)]]
    chunks.append({'id': 'summary', 'text': 'summary', 'vector': [.1, .2], 'metadata': {'file_id': 7, 'chunk_type': 'summary'}})
    if duplicate:
        chunks.append(chunks[0])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        if not missing:
            archive.writestr('documents/folder/a.txt', body)
        archive.writestr('vectors/chunks.jsonl', '\n'.join(json.dumps(c) for c in chunks))
    stream.seek(0)
    return zipfile.ZipFile(stream), manifest


def test_windows_paths_section_ids_and_summary_association(monkeypatch):
    archive, manifest = pack()
    _, owners, count = inspect_pack(archive, '', manifest)
    assert count == 3 and owners['summary'] == 'folder/a.txt'
    saved, file_ids = [], []
    monkeypatch.setattr(kb_pack.vector_store, 'upsert_chunks', lambda batch, **kw: saved.extend(batch))
    monkeypatch.setattr(kb_pack.bm25_index, 'add_chunks', lambda batch: None)
    monkeypatch.setattr(kb_pack.metadata_db, 'set_file_processed', lambda fid, ids: file_ids.extend(ids))
    kb_pack._import_vectors_from_pack(archive, '', 'collection', 'newkb', manifest['files'], {'folder/a.txt': 42}, None)
    assert len({c['id'] for c in saved}) == 3
    assert len(file_ids) == 2
    assert all(c['metadata']['file_id'] == 42 for c in saved)
    assert [c['metadata']['section_index'] for c in saved[:2]] == [0, 1]


@pytest.mark.parametrize('options', [{'missing': True}, {'duplicate': True}])
def test_invalid_pack_rejected(options):
    archive, manifest = pack(**options)
    with pytest.raises(ValueError):
        inspect_pack(archive, '', manifest)


@pytest.mark.parametrize('cache_size', [0, 1])
def test_failed_model_switch_restores_active_model(monkeypatch, cache_size):
    embedder = llm_client._LocalEmbedding('old', cache_size=cache_size)
    old = object()
    embedder._model, embedder._dimensions = old, 768
    def fail():
        embedder._model = None
        raise OSError('missing weights')
    monkeypatch.setattr(embedder, '_load', fail)
    with pytest.raises(OSError):
        embedder.switch_model('missing')
    assert (embedder.model_name, embedder._model, embedder.dimensions) == ('old', old, 768)
    assert not embedder._load_lock.locked()


def test_model_selection_updates_effective_override(monkeypatch, tmp_path):
    import yaml
    base, local = tmp_path/'config.yaml', tmp_path/'config.local.yaml'
    base.write_text('llm:\n  embedding:\n    local_model: base\n')
    local.write_text('llm:\n  embedding:\n    local_model: override\npaths:\n  feed_folder: /data/feeds\n')
    monkeypatch.setattr(config, 'USER_CONFIG_PATH', base)
    monkeypatch.setattr(config, 'USER_CONFIG_LOCAL_PATH', local)
    llm_client.LLMClient._save_local_model_to_config(object(), 'new')
    assert yaml.safe_load(local.read_text())['llm']['embedding']['local_model'] == 'new'
    assert yaml.safe_load(local.read_text())['paths']['feed_folder'] == '/data/feeds'


def test_empty_sync_response_logged_as_failure(monkeypatch):
    from src.core import accounts, ai_call_logger
    monkeypatch.setattr(accounts, 'enabled', False)
    records = []
    monkeypatch.setattr(ai_call_logger, 'log_call', lambda **kw: records.append(kw))
    client = object.__new__(llm_client.LLMClient)
    client._providers = {'test': SimpleNamespace(usable=True, chat_model='actual-model', chat=lambda *a, **k: '')}
    client._chat_chain = ['test']
    with pytest.raises(Exception, match='空文本'):
        client._chat_once([{'role': 'user', 'content': 'hi'}])
    assert records[-1]['success'] is False
    assert records[-1]['model'] == 'actual-model'


def test_derived_metadata_remaps_files_and_summary_snapshot(monkeypatch):
    from src.db import metadata_db as db
    from src.core import accounts
    from src.knowledge.pack_metadata import export_metadata, restore_metadata
    monkeypatch.setattr(accounts, 'enabled', False)
    monkeypatch.setattr(accounts, 'identity', SimpleNamespace(get=lambda: None))
    db.init_db()
    db.create_kb('pack-src', 'pack-src', 'pack-src')
    db.create_kb('pack-dst', 'pack-dst', 'pack-dst')
    first = db.upsert_file(relative_path='source.txt', absolute_path='source.txt', content_hash='abc', file_size=3, file_type='txt', kb_id='pack-src')
    second = db.upsert_file(relative_path='dest.txt', absolute_path='dest.txt', content_hash='abc', file_size=3, file_type='txt', kb_id='pack-dst')
    db.set_file_processed(first, ['old'])
    with db.get_cursor() as cur:
        cur.execute("INSERT INTO document_meta(file_id,kb_id,summary,created_at,updated_at) VALUES (?,?,'summary',1,1)", (first, 'pack-src'))
        cur.execute("INSERT INTO kb_concepts(kb_id,concept_name,source_file_ids_json,created_at,updated_at) VALUES ('pack-src','concept',?,1,1)", (json.dumps([first]),))
    db.update_kb_global_summary('pack-src', 'overview', 'model', 10, {str(first): 'abc'})
    data = export_metadata('pack-src', [{'id': first}])
    manifest = {'derived_metadata': data, 'files': [{'original_file_id': first, 'relative_path': 'a.txt', 'content_hash': 'abc'}]}
    restore_metadata(manifest, 'pack-dst', {'a.txt': second})
    assert json.loads(db.list_kb_concepts('pack-dst')[0]['source_file_ids_json']) == [second]
    assert db.get_kb_global_summary('pack-dst')['snapshot'] == {str(second): 'abc'}


@pytest.mark.parametrize('content,reason,ok', [([{'type':'text','text':'a'},{'type':'text','text':'b'}],'end_turn',True), ([], 'end_turn',False), ([{'type':'text','text':'half'}], 'max_tokens',False)])
def test_arch_response_completeness(monkeypatch, content, reason, ok):
    from src.core.llm_providers import AnthropicArchProvider, LLMError
    provider = AnthropicArchProvider('test', {'base_url':'https://example.test', 'chat_model':'test-model'}, 'dummy')
    response = SimpleNamespace(status_code=200, json=lambda: {'content':content,'stop_reason':reason,'usage':{'input_tokens':3,'output_tokens':2}})
    monkeypatch.setattr(type(provider), 'sync_client', property(lambda self: SimpleNamespace(post=lambda *a, **k: response)))
    if ok:
        result = provider.chat([{'role':'user','content':'hi'}])
        assert result == 'ab' and result.usage['output_tokens'] == 2
    else:
        with pytest.raises(LLMError):
            provider.chat([{'role':'user','content':'hi'}])


def test_optional_binding_preserves_original_permission_error(monkeypatch):
    from fastapi import HTTPException
    from src.core import accounts
    binding = accounts.provider_binding_error.set(None)
    selected = accounts.selected_provider.set(None)
    snapshot = accounts.provider_snapshot.set(None)
    try:
        monkeypatch.setattr(accounts, 'user', lambda: {'id':'u', 'default_provider':'disabled'})
        monkeypatch.setattr(accounts, 'rows', lambda *a, **k: [])
        accounts.bind_provider(required=False)
        with pytest.raises(HTTPException) as error:
            accounts.resolve_provider(accounts.selected_provider.get())
        assert error.value.status_code == 403
        assert '停用' in error.value.detail
    finally:
        accounts.provider_binding_error.reset(binding)
        accounts.selected_provider.reset(selected)
        accounts.provider_snapshot.reset(snapshot)
