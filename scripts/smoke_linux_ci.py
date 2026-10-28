"""Offline candidate smoke checks using disposable application data."""
import argparse
import json
import os
import subprocess
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    subprocess.run(['docker', 'run', '--rm', '--network', 'none', args.image,
                    'python', '-m', 'pip', 'check'], check=True)
    models = os.environ.get('INSIGHT_MODELS_DIR')
    if models:
        code = """import gc
import tiktoken
from sentence_transformers import SentenceTransformer, CrossEncoder
embedding = SentenceTransformer('BAAI/bge-large-zh-v1.5', device='cpu', local_files_only=True)
assert embedding.encode(['offline build verification']).shape == (1, 1024)
del embedding
gc.collect()
reranker = CrossEncoder('/models/reranker-torch', device='cpu', local_files_only=True)
assert len(reranker.predict([('question', 'answer')])) == 1
assert tiktoken.get_encoding('cl100k_base').encode('offline check')
assert tiktoken.get_encoding('o200k_base').encode('offline check')
print('Offline embedding, reranker and tokenizers: PASS')
"""
        subprocess.run(['docker', 'run', '--rm', '--network', 'none',
                        '--tmpfs', '/data:uid=1002,gid=1002', '-v', f'{models}:/models:ro',
                        args.image, 'python', '-c', code], check=True, timeout=300)
    name = 'insight-ci-' + uuid.uuid4().hex[:12]
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
    finally:
        subprocess.run(['docker', 'logs', name], check=False)
        subprocess.run(['docker', 'rm', '-f', name], check=False)


if __name__ == '__main__':
    main()
