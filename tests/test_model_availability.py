"""Model preflight must not load weights, download files or start offline rebuilds."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from huggingface_hub.errors import FileMetadataError, LocalEntryNotFoundError

from src.core import model_availability as models


@pytest.fixture(autouse=True)
def isolated_model_checks(monkeypatch):
    monkeypatch.setattr(models.constants, "HF_HUB_OFFLINE", False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setattr(models, "try_to_load_from_cache", lambda *a: None)


def model_files(root):
    root.mkdir()
    for name, content in {
        "config.json": {}, "tokenizer_config.json": {}, "tokenizer.json": {},
        "modules.json": [{"path": ""}, {"path": "1_Pooling"},
                         {"path": "2_Normalize", "type": "sentence_transformers.models.Normalize"}],
    }.items():
        (root / name).write_text(json.dumps(content))
    (root / "1_Pooling").mkdir()
    (root / "1_Pooling/config.json").write_text('{}')
    (root / "pytorch_model.bin").write_bytes(b'fake weights; never loaded')
    return root


def test_local_snapshot_and_partial_download(tmp_path, monkeypatch):
    root = model_files(tmp_path / "snapshot")
    monkeypatch.setattr(models, "try_to_load_from_cache", lambda *a: str(root / 'config.json'))
    monkeypatch.setattr(models, "get_hf_file_metadata", lambda *a, **k: pytest.fail("local check must not use network"))
    assert models.model_availability('BAAI/test', probe=True)['state'] == 'local'
    (root / "pytorch_model.bin").rename(root / "pytorch_model.bin.incomplete")
    assert not models.local_files_ready('BAAI/test')
    (root / "model.safetensors.index.json").write_text(json.dumps({'weight_map': {'x': 'part-1.safetensors', 'y': 'part-2.safetensors'}}))
    (root / 'part-1.safetensors').write_bytes(b'1')
    assert not models.local_files_ready('BAAI/test')
    (root / 'part-2.safetensors').write_bytes(b'2')
    assert models.local_files_ready('BAAI/test')
    (root / '1_Pooling/config.json').unlink()
    assert not models.local_files_ready('BAAI/test')


@pytest.mark.parametrize('flag', ['hub', 'transformers'])
def test_offline_missing_is_blocked_without_network(monkeypatch, flag):
    if flag == 'hub':
        monkeypatch.setattr(models.constants, 'HF_HUB_OFFLINE', True)
    else:
        monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    monkeypatch.setattr(models, 'get_hf_file_metadata', lambda *a, **k: pytest.fail('offline network request'))
    result = models.model_availability('BAAI/missing', probe=True)
    assert result['state'] == 'offline_missing' and not result['can_switch']
    assert models.model_availability('BAAI/missing', in_memory=True, probe=True)['can_switch']


def test_status_does_not_probe_and_precheck_checks_metadata(monkeypatch):
    calls = []
    def metadata(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(commit_hash='revision', etag='hash', size=1000)
    monkeypatch.setattr(models, 'get_hf_file_metadata', metadata)
    assert models.model_availability('BAAI/missing')['state'] == 'download_required'
    assert not calls
    checked = models.model_availability('BAAI/missing', probe=True)
    assert checked['can_switch'] and '完整下载仍取决于' in checked['message']
    assert calls == [{'token': False, 'timeout': 5}]
    monkeypatch.setattr(models, 'get_hf_file_metadata', lambda *a, **k: SimpleNamespace(commit_hash=None, etag=None, size=None))
    assert models.model_availability('BAAI/missing', probe=True)['state'] == 'download_unavailable'


def test_download_failure_explains_nested_metadata_error_without_secrets(monkeypatch):
    inner = FileMetadataError('https://user:private-password@host/secret')
    outer = LocalEntryNotFoundError('private-token')
    outer.__cause__ = inner
    assert '版本或文件信息' in models.download_error_message(outer)
    assert 'private' not in models.download_error_message(outer)
    def fail(*a, **k):
        raise outer
    monkeypatch.setattr(models, 'get_hf_file_metadata', fail)
    assert not models.model_availability('BAAI/missing', probe=True)['can_switch']


@pytest.mark.parametrize('endpoint', ['embedding_switch', 'embedding_precheck', 'embedding_rebuild_and_switch'])
def test_offline_endpoint_rejects_before_mutating_or_starting_rebuild(monkeypatch, endpoint):
    from src.api import main
    from src.knowledge import rebuild
    monkeypatch.setattr(models.constants, 'HF_HUB_OFFLINE', True)
    client = SimpleNamespace(get_embedding_status=lambda: {'available_models': [{'name': 'BAAI/missing'}], 'cached_models': []})
    monkeypatch.setattr(main.llm_client, 'get_client', lambda: client)
    monkeypatch.setattr(rebuild, 'is_rebuilding', lambda: False)
    monkeypatch.setattr(rebuild, 'precheck', lambda *a: pytest.fail('must not query or mutate data'))
    with pytest.raises(HTTPException) as exc:
        getattr(main, endpoint)(main.EmbeddingSwitchRequest(model_name='BAAI/missing'))
    assert exc.value.status_code == 409
    assert '离线模式' in exc.value.detail
