"""Isolated model-loading policy; no downloads or real model state."""
import sys
from types import SimpleNamespace

import pytest

from src.core import llm_client as llm, model_availability as availability


@pytest.fixture
def loader(monkeypatch):
    calls = []
    model = SimpleNamespace(get_sentence_embedding_dimension=lambda: 768)
    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return model
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=create))
    monkeypatch.setattr(availability, 'local_files_ready', lambda name: True)
    return calls, model


@pytest.mark.parametrize('cache_size', [0, 1])
def test_cached_snapshot_load_does_not_probe_hub(loader, monkeypatch, tmp_path, cache_size):
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, 'try_to_load_from_cache', lambda *a: str(tmp_path/'config.json'))
    embedder = llm._LocalEmbedding('org/model', cache_size=cache_size)
    embedder._load()
    calls, model = loader
    assert calls == [((str(tmp_path),), {'device': 'cpu', 'local_files_only': True})]
    assert embedder._model is model and embedder.dimensions == 768


def test_missing_model_fails_fast_without_implicit_download(loader, monkeypatch):
    monkeypatch.setattr(availability, 'local_files_ready', lambda name: False)
    embedder = llm._LocalEmbedding('org/missing')
    with pytest.raises(llm.EmbeddingLoadError) as first:
        embedder._load()
    assert first.value.code == 'embedding_files_missing'
    with pytest.raises(llm.EmbeddingLoadError) as again:
        embedder._load()
    assert 0 < again.value.retry_after_seconds <= 60
    assert loader[0] == []
    # Explicit admin loading bypasses the cooldown and permits download.
    assert embedder.switch_model('org/missing')
    assert loader[0][0][1]['local_files_only'] is False


def test_failed_loading_cools_down_and_recovers(loader, monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(llm.time, 'monotonic', lambda: clock[0])
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise OSError('private url and credential must not reach MCP')
    module = sys.modules['sentence_transformers']
    success = module.SentenceTransformer
    monkeypatch.setattr(module, 'SentenceTransformer', fail)
    embedder = llm._LocalEmbedding(str(tmp_path))
    for _ in range(3):
        with pytest.raises(llm.EmbeddingLoadError) as exc:
            embedder._load()
        assert 'private' not in str(exc.value)
    assert len(attempts) == 1
    clock[0] += 61
    monkeypatch.setattr(module, 'SentenceTransformer', success)
    embedder._load()
    assert embedder.dimensions == 768
    assert not embedder._load_failures


def test_prewarm_reports_failure_and_skips_disabled_cache(monkeypatch):
    client = object.__new__(llm.LLMClient)
    def fail():
        raise llm.EmbeddingLoadError(60)
    embedder = SimpleNamespace(_cache_size=1, _load=fail)
    monkeypatch.setattr(client, '_get_local_embedder', lambda: embedder)
    with pytest.raises(llm.EmbeddingLoadError):
        client.prewarm_embedding()
    embedder._cache_size = 0
    assert client.prewarm_embedding() is False
