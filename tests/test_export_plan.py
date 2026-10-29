"""Partial packs remain internally consistent; source data is never changed."""
import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest

from src.knowledge import export_plan, kb_pack, pack_metadata


@pytest.fixture
def source(monkeypatch, tmp_path):
    good = tmp_path / 'good.txt'
    good.write_text('good')
    files = [dict(id=1, absolute_path=str(good), relative_path='kb\\good.txt', status='done',
                  content_hash=hashlib.sha256(b'good').hexdigest(), file_size=4, chunk_count=1, chunk_ids_json='["a"]', file_type='txt'),
             dict(id=2, absolute_path=str(tmp_path / 'missing.txt'), relative_path='kb\\missing.txt', status='done',
                  content_hash='missing-hash', file_size=1, chunk_count=1, chunk_ids_json='["b"]', file_type='txt')]
    monkeypatch.setattr(export_plan.db, 'list_files', lambda **kw: files)
    monkeypatch.setattr(export_plan.db, 'init_db', lambda: None)
    monkeypatch.setattr(export_plan.db, 'get_kb', lambda kid: dict(name='KB', source='local', collection_name='collection', embedding_model='test', embedding_dim=2))
    monkeypatch.setattr(export_plan.db, 'update_kb', lambda *a, **kw: None)
    monkeypatch.setattr(pack_metadata, 'export_metadata', lambda *a: dict(documents=[dict(file_id=1, summary='good summary', concepts_extracted=1, concepts_count=4)], concepts=[{}], relations=[{}], global_summary={'summary': 'includes missing'}))
    class Collection:
        def count(self): return 2
        def get(self, **kw):
            if kw.get('limit') == 1:
                return {'embeddings': [[1., 0.]]}
            return dict(ids=['a', 'b'], documents=['good', 'missing'], embeddings=[[1., 0.], [0., 1.]], metadatas=[{'file_id': 1}, {'file_id': 2}])
    monkeypatch.setattr(kb_pack.vector_store, 'get_chroma_client', lambda: SimpleNamespace(get_collection=lambda name: Collection()))
    return files, tmp_path


def test_preflight_reports_all_and_rejects_unconfirmed_or_stale_export(source):
    files, folder = source
    available, report = export_plan.inspect_sources('kb')
    assert len(available) == report['available'] == 1
    assert report['excluded'][0]['file_id'] == 2
    assert 'absolute_path' not in json.dumps(report)
    with pytest.raises(ValueError, match='无法完整导出'):
        kb_pack.export_kb_to_zip('kb', folder / 'full.zip')
    with pytest.raises(ValueError, match='先预检'):
        kb_pack.export_kb_to_zip('kb', folder / 'partial.zip', allow_partial=True)
    files[0]['status'] = 'failed'
    with pytest.raises(ValueError, match='状态已变化'):
        kb_pack.export_kb_to_zip('kb', folder / 'stale.zip', allow_partial=True, expected_token=report['token'])


def test_partial_pack_filters_vectors_and_derived_metadata(source):
    files, folder = source
    before = json.dumps(files)
    _, report = export_plan.inspect_sources('kb')
    target = folder / 'partial.zip'
    kb_pack.export_kb_to_zip('kb', target, allow_partial=True, expected_token=report['token'])
    with zipfile.ZipFile(target) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert len(manifest['files']) == manifest['stats']['document_count'] == 1
        assert manifest['files'][0]['relative_path'] == 'kb/good.txt'
        assert 'documents/kb/missing.txt' not in z.namelist()
        assert [json.loads(line)['id'] for line in z.read('vectors/chunks.jsonl').splitlines()] == ['a']
        meta = manifest['derived_metadata']
        assert meta['global_summary'] is None and meta['concepts'] == meta['relations'] == []
        assert meta['documents'][0]['summary'] == 'good summary'
        assert json.loads(z.read('export-report.json'))['excluded'][0]['file_id'] == 2
    assert json.dumps(files) == before
