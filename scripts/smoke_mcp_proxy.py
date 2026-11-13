"""Candidate-only real app -> gateway -> Nginx check, with disposable data supplied by CI."""
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def run(*args):
    return subprocess.run(['docker', *args], check=True, stdout=subprocess.DEVNULL)


def check(app, mcp_image, web_image, version):
    name = 'insight-mcp-proxy-' + uuid.uuid4().hex[:12]
    gateway, proxy = name + '-mcp', name + '-web'
    config = Path(__file__).resolve().parents[1] / 'deploy/linux/nginx.conf'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        run('network', 'create', name)
        run('network', 'connect', '--alias', 'app', name, app)
        run('run', '-d', '--name', gateway, '--network', name, '--network-alias', 'mcp', '--read-only',
            '--tmpfs', '/tmp:size=16m', '-e', 'INSIGHT_BACKEND_URL=http://app:8888',
            '-e', 'INSIGHT_MCP_ALLOWED_HOSTS=kb-ci.test:8088', mcp_image)
        run('run', '-d', '--name', proxy, '--network', name, '--read-only', '--user', '1002:1002',
            '--tmpfs', '/tmp:size=64m,mode=1777', '-p', '127.0.0.1::8088',
            '-v', str(config) + ':/etc/nginx/nginx.conf:ro', '--entrypoint', 'nginx', web_image,
            '-e', 'stderr', '-g', 'daemon off;')
        info = json.loads(subprocess.check_output(['docker', 'inspect', proxy]))[0]
        origin = 'http://127.0.0.1:' + info['NetworkSettings']['Ports']['8088/tcp'][0]['HostPort']
        def request(path, payload=None, headers=None):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(origin + path, data=data,
                                         headers={'Host': 'kb-ci.test:8088', 'Content-Type': 'application/json', **(headers or {})})
            try:
                return opener.open(req, timeout=5)
            except urllib.error.HTTPError as error:
                return error
        for _ in range(40):
            try:
                with request('/mcp-health') as response:
                    value = json.load(response)
                    if response.code == 200 and value.get('version') == version and value.get('component') == 'insight-mcp':
                        break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Proxy did not route to paired MCP gateway')
        with request('/mcp', {}) as response:
            assert response.code == 401 and response.headers.get('WWW-Authenticate') == 'Bearer'
        for path in ('/mcp-service/check', '/mcp/'):
            with request(path) as response:
                assert response.code == 404
        with request('/') as response:
            assert response.code == 200 and b'<html' in response.read().lower()
        # The caller created this application on an empty tmpfs. Explicitly refuse
        # initialization if it already contains an account; never use real data.
        fixture = '''import json
from pathlib import Path
from src.db import metadata_db as db
from src.core import accounts as a
from src.mcp import store
assert not a.rows('SELECT 1 FROM auth_users LIMIT 1'), 'Only an empty candidate app is allowed'
a.init_auth()
a.bootstrap('candidate-check', 'disposable-candidate-password', (Path('/data')/'setup-token.txt').read_text().strip())
user = a.rows('SELECT * FROM auth_users WHERE username=?', ('candidate-check',))[0]
a.identity.set(user)
db.create_kb('candidate-mcp', 'Candidate MCP fixture', '')
a.claim('kb', 'candidate-mcp')
store.save_config(store.Settings(enabled=True, rerank_enabled=False))
print(json.dumps(store.create_token('temporary CI check', ['candidate-mcp'], 1)))
'''
        output = subprocess.check_output(['docker', 'exec', app, 'python', '-c', fixture], text=True)
        secret = json.loads(output.splitlines()[-1])['token']
        headers = {'Authorization': 'Bearer ' + secret, 'Accept': 'application/json, text/event-stream'}
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'release-check', 'version': '1'}}}
        with request('/mcp', init, headers) as response:
            assert response.code == 200
            assert json.load(response)['result']['serverInfo']['version'] == version
            headers['Mcp-Session-Id'] = response.headers['mcp-session-id']
            headers['Mcp-Protocol-Version'] = '2025-06-18'
        with request('/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers) as response:
            assert response.code == 202
        with request('/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, headers) as response:
            assert any(tool['name'] == 'list_knowledge_bases' for tool in json.load(response)['result']['tools'])
        with request('/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'list_knowledge_bases', 'arguments': {}}}, headers) as response:
            result = json.load(response)['result']
            assert not result.get('isError')
            assert result['structuredContent']['knowledge_bases'][0]['kb_id'] == 'candidate-mcp'
        print('Real app + independent gateway + Nginx: initialize/list/query, protected routes and frontend PASS')
    finally:
        for container in (proxy, gateway):
            subprocess.run(['docker', 'logs', container], check=False)
            subprocess.run(['docker', 'rm', '-f', container], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'network', 'disconnect', name, app], check=False)
        subprocess.run(['docker', 'network', 'rm', name], check=False, stdout=subprocess.DEVNULL)
