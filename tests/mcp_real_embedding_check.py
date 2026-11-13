"""Opt-in synthetic MCP retrieval using a read-only, already deployed model snapshot.

Run with --model-dir PATH. All databases/configuration are temporary. No chat API.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    args = parser.parse_args()
    model_path = str(Path(args.model_dir).resolve())
    with tempfile.TemporaryDirectory(prefix='mcp-real-embedding-') as folder:
        (Path(folder)/'data').mkdir()
        (Path(folder)/'logs').mkdir()
        os.environ.update(AMD_DATA_DIR=folder, HF_HOME=str(Path(folder)/'hf'),
                         ANONYMIZED_TELEMETRY='False', TOKENIZERS_PARALLELISM='false',
                         OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
        from src.core import accounts as a, llm_client, vector_store
        from src.core.config import settings
        from src.db import metadata_db as db
        from src.knowledge import reading_index as ri
        from src.mcp import store, tools
        from src.mcp.contracts import request_identity
        from src.qa import bm25_index
        # Network is denied even though HF_HUB_OFFLINE is not needed for local paths.
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')) as network:
            settings.config['llm']['embedding'].update(local_model=model_path, cache_size=1, device='cpu')
            client = llm_client.get_client()
            started = time.perf_counter()
            assert client.prewarm_embedding()
            load_ms = (time.perf_counter()-started)*1000
            bodies = ['Firmware update procedure: verify the image checksum, connect the device, then flash. If flashing fails, check the cable and power supply.',
                      'Bond records include issue date, maturity date and interest rate.',
                      'Lunch menu: rice, vegetables, tea and fruit.']
            from tests.mcp_relevance_check import CASES
            matrix = [case for case in CASES if case[0] in ('short_fact', 'code', 'negation', 'version_conflict', 'table_fact', 'punctuation', 'finance')]
            bodies += [case[2] for case in matrix]
            embeddings, _ = client.embed(bodies)
            assert len(embeddings) == len(bodies) and len(embeddings[0]) > 0
            assert network.call_count == 0
            db.init_db()
            a.init_auth()
            a.bootstrap('mcp-real-check', 'temporary-test-password', (Path(folder)/'setup-token.txt').read_text().strip())
            identity = a.identity.set(a.rows('SELECT * FROM auth_users WHERE username=?', ('mcp-real-check',))[0])
            kid, collection = 'synthetic_mcp', 'synthetic_mcp_vectors'
            db.create_kb(kid, 'Synthetic', collection, embedding_model=model_path, embedding_dim=len(embeddings[0]))
            names = ['firmware.txt', 'bonds.txt', 'menu.txt'] + [case[0]+'.txt' for case in matrix]
            for i, (name, body, embedding) in enumerate(zip(names, bodies, embeddings)):
                path = Path(folder)/name
                path.write_text(body)
                fid = db.upsert_file(name, str(path), ri.fingerprint(path), path.stat().st_size, 'txt', kb_id=kid)
                chunk = f'chunk_{i}'
                db.set_file_processed(fid, [chunk])
                file = db.get_file_by_path(name, kid)
                builder = ri.build_file(file)
                with db.get_cursor() as cur:
                    builder.publish(cur, fid, file['content_hash'])
                chunks = [{'id': chunk, 'text': body, 'embedding': embedding,
                    'metadata': {'file_id': fid, 'content_hash': file['content_hash'], 'chunk_index': 0,
                                 'section_index': 0, 'chunk_type': 'content', 'source_path': name, 'kb_id': kid}}]
                vector_store.upsert_chunks(chunks, collection)
                bm25_index.add_chunks(chunks)
            store.save_config(store.Settings(enabled=True, requests_per_minute=120))
            created = store.create_token('isolated check', [kid], 1)
            token = store.authenticate(created['token'])
            results = []
            for mode, query, variants in [('semantic', '固件升级失败如何排查', []),
                    ('semantic', '固件升级失败如何排查', ['firmware flashing failure cable power']),
                    ('hybrid', '固件升级失败如何排查', ['firmware flashing failure cable power'])]:
                for repeat in range(3):
                    binding = request_identity.set(f'isolated-{mode}-{len(results)}')
                    started = time.perf_counter()
                    result = tools.execute(token, 'search_knowledge', {'kb_id': kid, 'query': query,
                        'match_mode': mode, 'query_variants': variants, 'limit': 3}, lambda: None)
                    elapsed = (time.perf_counter()-started)*1000
                    request_identity.reset(binding)
                    assert 'vector' in result['search_scope']['executed_paths'], result
                    assert not result['retrieval_warnings'], result
                    found = [ev['name'] for ev in result['evidence']]
                    assert 'firmware.txt' in found, result
                    assert 'bonds.txt' not in found, result
                    assert result['relevance']['status'] == 'assessed', result
                    results.append({'mode': mode, 'variants': bool(variants), 'repeat': repeat+1,
                        'elapsed_ms': round(elapsed, 2), 'sources': found,
                        'phases': result['diagnostics']['phase_ms']})
            checks = []
            for case, query, body, relevant in matrix + [('no_answer', '报销审批需要几个人签字', '', False)]:
                binding = request_identity.set('matrix-'+case)
                result = tools.execute(token, 'search_knowledge', {'kb_id': kid, 'query': query, 'match_mode': 'hybrid', 'limit': 5}, lambda: None)
                request_identity.reset(binding)
                found = [ev['name'] for ev in result['evidence']]
                assert result['relevance']['status'] == 'assessed', result
                if relevant:
                    assert case+'.txt' in found, (case, result)
                else:
                    assert not found and result['outcome'] == 'no_match', result
                checks.append({'case': case, 'sources': found, 'relevance': result['relevance']})
            missing = llm_client._LocalEmbedding(str(Path(folder)/'missing-model'))
            degraded = []
            with patch('src.mcp.ranked_search.llm_client.get_client', return_value=SimpleNamespace(embed=lambda texts: (missing.embed(texts), 'local'))):
                for repeat in range(3):
                    binding = request_identity.set(f'isolated-missing-{repeat}')
                    started = time.perf_counter()
                    result = tools.execute(token, 'search_knowledge', {'kb_id': kid,
                        'query': 'firmware flashing failure', 'match_mode': 'semantic'}, lambda: None)
                    elapsed = (time.perf_counter()-started)*1000
                    request_identity.reset(binding)
                    assert result['search_scope']['executed_paths'] == ['keyword']
                    assert result['search_scope']['degraded']
                    assert result['outcome'] == 'partial' and result['evidence'], result
                    assert elapsed < 2000, 'missing model should not wait for network retries'
                    degraded.append({'elapsed_ms': round(elapsed, 2), 'code': result['retrieval_warnings'][0]['code']})
            a.identity.reset(identity)
        print(json.dumps({'load_ms': round(load_ms, 2), 'dimensions': len(embeddings[0]),
                          'synthetic_only': True, 'results': results, 'matrix': checks, 'missing_model': degraded}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
