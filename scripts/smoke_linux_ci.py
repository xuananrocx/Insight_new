"""Offline candidate smoke checks using disposable application data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--mcp-image', required=True)
    parser.add_argument('--web-image', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', args.image,
                    'python', '-m', 'pip', 'check'], check=True)
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', args.mcp_image,
                    'python', '-m', 'pip', 'check'], check=True)
    tests = Path(__file__).resolve().parents[1] / 'tests'
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-e', 'PYTHONPATH=/app',
                    '-v', f'{tests}:/validation/tests:ro', args.mcp_image,
                    'python', '/validation/tests/mcp_gateway_check.py'], check=True, timeout=90)
    regression_files = [
        'api_models', 'accounts_access', 'permissions', 'session_organization',
        'session_kb_switch', 'api_retry', 'batch_upload_stages', 'import_cancel',
        'reading_indexes', 'ai_generation_config', 'summary_jobs',
        'summary_staleness', 'summary_stream_policy', 'global_summary_backfill',
        'summarize_map_reduce', 'deployment_regressions',
        'answer_jobs', 'export_plan', 'model_availability', 'tool_agent',
        'web_search', 'team_api_stats', 'retrieval_timeout',
    ]
    for group in (regression_files, ['default_kb']):
        subprocess.run(['docker', 'run', '--rm', '--network', 'none',
                        '--tmpfs', '/data:uid=1002,gid=1002', '-e', 'PYTHONPATH=/app',
                        '-v', f'{tests}:/validation/tests:ro', args.image,
                        'python', '-m', 'pytest', '-q', '--asyncio-mode=auto', '-p', 'no:cacheprovider',
                        *[f'/validation/tests/test_{name}.py' for name in group]],
                       check=True, timeout=600)
    models = os.environ.get('INSIGHT_MODELS_DIR')
    if models:
        code = """import gc
import tiktoken
from sentence_transformers import SentenceTransformer, CrossEncoder
embedding = SentenceTransformer('BAAI/bge-large-zh-v1.5', device='cpu', local_files_only=True)
assert embedding.encode(['offline build verification']).shape == (1, 1024)
del embedding
gc.collect()
from src.mcp import relevance
assert str(relevance.model_path()) == '/models/reranker-torch'
reranker = CrossEncoder(str(relevance.model_path()), device='cpu', local_files_only=True)
assert len(reranker.predict([('question', 'answer')])) == 1
relevance._model = reranker
assert len(relevance.score('question', ['answer'])) == 1
assert tiktoken.get_encoding('cl100k_base').encode('offline check')
assert tiktoken.get_encoding('o200k_base').encode('offline check')
print('Offline embedding, reranker and tokenizers: PASS')
"""
        subprocess.run(['docker', 'run', '--rm', '--network', 'none',
                        '--tmpfs', '/data:uid=1002,gid=1002', '-v', f'{models}:/models:ro',
                        args.image, 'python', '-c', code], check=True, timeout=300)
    name = 'insight-ci-' + uuid.uuid4().hex[:12]
    gateway = name + '-mcp'
    try:
        subprocess.run(['docker', 'run', '-d', '--name', name,
                        '--tmpfs', '/data:uid=1002,gid=1002',
                        '-p', '127.0.0.1::8888', args.image], check=True)
        info = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        port = info['NetworkSettings']['Ports']['8888/tcp'][0]['HostPort']
        for _ in range(60):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                    assert response.status == 200
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                    assert response.status == 200
                    assert b'<html' in response.read().lower()
                print('Candidate health and frontend: PASS')
                break
            except (OSError, AssertionError):
                time.sleep(2)
        else:
            raise RuntimeError('Candidate did not become healthy')
        # Exercise the real paired image via host networking, as used by legacy hosts.
        # The application data is the disposable tmpfs above; never a user directory.
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            gateway_port = probe.getsockname()[1]
        subprocess.run(['docker', 'run', '-d', '--name', gateway, '--network', 'host',
                        '--read-only', '--tmpfs', '/tmp:size=16m',
                        '-e', f'INSIGHT_BACKEND_URL=http://127.0.0.1:{port}',
                        '-e', 'INSIGHT_MCP_BIND=127.0.0.1', '-e', f'INSIGHT_MCP_PORT={gateway_port}',
                        args.mcp_image], check=True)
        for _ in range(30):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{gateway_port}/health', timeout=3) as response:
                    value = json.load(response)
                    assert value['status'] == 'ok' and value['component'] == 'insight-mcp'
                break
            except (OSError, AssertionError):
                time.sleep(1)
        else:
            raise RuntimeError('Paired MCP gateway did not become ready')
        try:
            urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{gateway_port}/mcp', data=b'{}'), timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 401 and error.headers.get('WWW-Authenticate') == 'Bearer'
        else:
            raise RuntimeError('MCP route must require a token')
        print('Paired application and isolated MCP image readiness/auth: PASS')
        from smoke_mcp_proxy import check
        candidate_version = subprocess.check_output(['docker', 'exec', name, 'python', '-c',
                                                     'from src import __version__; print(__version__)'], text=True).strip()
        check(name, args.mcp_image, args.web_image, candidate_version)
        from smoke_host_deployment import check as check_host
        check_host(args.image, args.web_image, args.bundle.resolve())
    finally:
        subprocess.run(['docker', 'logs', gateway], check=False)
        subprocess.run(['docker', 'rm', '-f', gateway], check=False)
        subprocess.run(['docker', 'logs', name], check=False)
        subprocess.run(['docker', 'rm', '-f', name], check=False)


if __name__ == '__main__':
    main()
