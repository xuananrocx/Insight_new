"""Exercise the packaged host controller against disposable real Linux containers.

A host-side adapter invokes the test Nginx container, so the same controller path
uses the runner's shell/Docker and real Nginx validation/reload through chroot.
No production address, socket override or user directory is accepted.
"""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid


def run(*args):
    return subprocess.run(list(args), check=True, stdout=subprocess.DEVNULL)


def check(app_image, web_image, bundle):
    name = 'insight-host-check-' + uuid.uuid4().hex[:12]
    folder = Path(tempfile.mkdtemp(prefix=name + '-')).resolve()
    app_name, proxy_name = name + '-app', name + '-proxy'
    sockets = [socket.socket() for _ in range(3)]
    try:
        for probe in sockets:
            probe.bind(('127.0.0.1', 0))
        app_port, mcp_port, web_port = [probe.getsockname()[1] for probe in sockets]
    finally:
        for probe in sockets:
            probe.close()
    try:
        data, models = folder / 'data', folder / 'models'
        data.mkdir()
        models.mkdir()
        run('sudo', 'chown', '1002:1002', str(data))
        # Other image users need to traverse the fixture directory.
        folder.chmod(0o755)
        run('docker', 'run', '-d', '--name', app_name, '--network', 'host', '--user', '1002:1002',
            '-v', str(data) + ':/data', '-v', str(models) + ':/models', app_image,
            'python', '-m', 'uvicorn', 'src.api.main:app', '--host', '127.0.0.1', '--port', str(app_port))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:{}/health'.format(app_port), timeout=2) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError('Disposable host application failed to start')
        conf = folder / 'nginx.conf'
        server_conf = folder / 'insight-server.conf'
        include = folder / 'insight-mcp.conf'
        original = '''pid /tmp/nginx.pid;
events { worker_connections 64; }
http {
  client_body_temp_path /tmp/client;
  proxy_temp_path /tmp/proxy;
  include SERVER;
}
'''.replace('SERVER', str(server_conf))
        original_server = '''server {
    listen WEB;
    location / { proxy_pass http://127.0.0.1:APP; }
}
'''.replace('WEB', str(web_port)).replace('APP', str(app_port))
        conf.write_text(original)
        server_conf.write_text(original_server)
        candidate = folder / 'nginx-reviewed.conf'
        candidate.write_text(original_server.replace('    listen ', '    include ' + str(include) + ';\n    listen '))
        run('docker', 'run', '-d', '--name', proxy_name, '--network', 'host', '--read-only',
            '--tmpfs', '/tmp:size=16m', '-v', str(folder) + ':' + str(folder) + ':ro',
            '--entrypoint', 'nginx', web_image, '-p', str(folder), '-c', str(conf), '-g', 'daemon off;')
        adapter = folder / 'nginx-host-adapter'
        docker = shutil.which('docker')
        adapter.write_text('#!/bin/sh\nexec "' + docker + '" exec ' + proxy_name + ' nginx "$@"\n')
        adapter.chmod(0o755)
        settings = dict(deployment_mode='host', project=name, app_container=app_name, mcp_container=name + '-mcp',
                        app_user='1002:1002', docker_socket='/run/docker.sock', app_port=app_port, mcp_port=mcp_port,
                        public_origin='http://127.0.0.1:' + str(web_port), public_host='kb-ci.test:' + str(web_port),
                        mcp_allowed_hosts='kb-ci.test:' + str(web_port), data_dir=str(data), models_dir=str(models),
                        backup_dir=str(folder / 'backups'), data_schema='insight-0.1.1', nginx_binary=str(adapter),
                        nginx_prefix=str(folder), nginx_config=str(conf), nginx_server_config=str(server_conf), nginx_include=str(include))
        settings_path = folder / 'settings.json'
        settings_path.write_text(json.dumps(settings))
        state = folder / 'state'
        def controller(action, *extra):
            run('sh', str(bundle / 'hostctl.sh'), action, '--settings', str(settings_path), '--state', str(state), *extra)
        controller('adopt', '--apply')
        controller('check', '--bundle', str(bundle), '--nginx-candidate', str(candidate))
        controller('upgrade', '--apply', '--bundle', str(bundle), '--nginx-candidate', str(candidate))
        active = json.loads(subprocess.check_output(['sudo', 'cat', str(state / 'active.json')]))
        assert active['gateway']['State']['Running']
        assert active['app']['State']['Running']
        backups = subprocess.check_output(['sudo', 'find', str(folder / 'backups'), '-name', 'activation.json'], text=True).splitlines()
        assert len(backups) == 1
        controller('rollback', '--apply', '--backup', str(Path(backups[0]).parent))
        assert conf.read_text() == original and server_conf.read_text() == original_server and not include.exists()
        current = json.loads(subprocess.check_output(['docker', 'inspect', app_name]))[0]
        assert current['State']['Running']
        print('Packaged host controller: real adopt/check/upgrade/proxy/restart/rollback PASS')
    finally:
        ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=' + name], text=True).split()
        for cid in ids:
            subprocess.run(['docker', 'logs', '--tail', '30', cid], check=False)
        if ids:
            subprocess.run(['docker', 'rm', '-f', *ids], check=False, stdout=subprocess.DEVNULL)
        run('sudo', 'rm', '-rf', '--', str(folder))
