"""Manage an existing host-network app and a paired MCP container, without Compose.

Invoked by insightctl.py with deployment_mode=host. State contains private container
configuration; never publish it. All daemon access uses one explicit Unix socket.
"""
import base64
import copy
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tarfile
import time
from urllib.parse import quote
import urllib.request

import insightctl as common


def host_path(value):
    """The packaged runner mounts the host at /host; direct Python remains supported."""
    root = os.environ.get('INSIGHT_HOST_ROOT', '')
    return Path(root + str(value)) if root else Path(value)


class Engine:
    def __init__(self, path):
        self.path = path

    def request(self, method, path, body=None, raw=False):
        connection = http.client.HTTPConnection('localhost', timeout=180)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(180)
        connection.sock.connect(self.path)
        encoded = json.dumps(body).encode() if body is not None else None
        try:
            connection.request(method, path, body=encoded, headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if response.status >= 400:
                # Daemon errors can include private configuration: don't echo them.
                raise ValueError('Docker API {} {} failed ({})'.format(method, path.split('?')[0], response.status))
            return data if raw else json.loads(data) if data else None
        finally:
            connection.close()

    def inspect(self, name):
        return self.request('GET', '/containers/' + quote(name, safe='') + '/json')

    def action(self, name, action):
        return self.request('POST', '/containers/' + quote(name, safe='') + '/' + action)

    def create(self, name, config, host):
        return self.request('POST', '/containers/create?name=' + quote(name, safe=''), {**config, 'HostConfig': host})['Id']

    def logs(self, name):
        payload = self.request('GET', '/containers/' + quote(name, safe='') + '/logs?stdout=1&stderr=1&tail=100', raw=True)
        output = []
        while len(payload) >= 8 and payload[0] in (1, 2) and payload[1:4] == b'\0\0\0':
            size = int.from_bytes(payload[4:8], 'big')
            output.append(payload[8:8+size])
            payload = payload[8+size:]
        return b''.join(output + [payload]).decode(errors='replace')

    def load(self, archive):
        connection = http.client.HTTPConnection('localhost', timeout=300)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(300)
        connection.sock.connect(self.path)
        try:
            connection.putrequest('POST', '/images/load?quiet=1')
            connection.putheader('Content-Type', 'application/x-tar')
            connection.putheader('Content-Length', str(archive.stat().st_size))
            connection.endheaders()
            with archive.open('rb') as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    connection.send(block)
            response = connection.getresponse()
            payload = response.read()
            if response.status != 200 or any(json.loads(line).get('error') or json.loads(line).get('errorDetail') for line in payload.splitlines() if line):
                raise ValueError('Docker image load failed')
        finally:
            connection.close()


def config_for_app(snapshot, image):
    config = copy.deepcopy(snapshot['Config'])
    config['Image'] = image
    host = copy.deepcopy(snapshot['HostConfig'])
    if host.get('NetworkMode') != 'host' or host.get('AutoRemove'):
        raise ValueError('Only persistent host-network application containers can be adopted')
    return config, host


def gateway_spec(settings, release):
    name = settings['mcp_container']
    config = {
        'Image': release['image_ids'][release['mcp_image']], 'User': settings['app_user'],
        'WorkingDir': '/app', 'Cmd': ['python', '-m', 'src.mcp.gateway'],
        'Env': ['INSIGHT_BACKEND_URL=http://127.0.0.1:' + str(settings['app_port']),
                'INSIGHT_MCP_BIND=127.0.0.1', 'INSIGHT_MCP_PORT=' + str(settings['mcp_port']),
                'INSIGHT_MCP_ALLOWED_HOSTS=' + settings['mcp_allowed_hosts']],
        'Labels': {'io.insight.deployment': settings['project'], 'io.insight.component': 'mcp',
                   'org.opencontainers.image.revision': release['revision']},
    }
    host = {'NetworkMode': 'host', 'ReadonlyRootfs': True, 'Tmpfs': {'/tmp': 'rw,noexec,nosuid,size=16m'},
            'RestartPolicy': {'Name': 'unless-stopped'}, 'Memory': 256 * 1024**2, 'PidsLimit': 64,
            'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '20m', 'max-file': '3'}}}
    return name, config, host


def proxy_text(port):
    return '''# Managed Insight MCP routes; include inside the existing Insight server block.
location = /mcp {
    proxy_pass http://127.0.0.1:PORT/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 150s;
    client_max_body_size 16k;
}
location = /mcp-health {
    proxy_pass http://127.0.0.1:PORT/health;
    proxy_read_timeout 5s;
}
location ^~ /mcp/ { return 404; }
location ^~ /mcp-service { return 404; }
'''.replace('PORT', str(port))


def file_snapshot(path):
    path = host_path(path)
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def restore_file(path, value):
    path = host_path(path)
    if value is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.with_name(path.name + '.insight-tmp')
        temporary.write_bytes(base64.b64decode(value))
        temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        temporary.replace(path)


def nginx(settings, action):
    def enter_host():
        os.chroot(os.environ['INSIGHT_HOST_ROOT'])
        os.chdir('/')
    common.run([settings['nginx_binary'], '-p', settings['nginx_prefix'], '-c', settings['nginx_config'],
                *(['-t'] if action == 'test' else ['-s', 'reload'])],
               preexec_fn=enter_host if os.environ.get('INSIGHT_HOST_ROOT') else None)


def validate(settings):
    required = ('project', 'app_container', 'mcp_container', 'app_user', 'docker_socket',
                'data_dir', 'models_dir', 'backup_dir', 'nginx_config', 'nginx_include', 'nginx_binary',
                'nginx_prefix', 'public_origin', 'public_host', 'mcp_allowed_hosts', 'data_schema')
    if any(not settings.get(key) for key in required):
        raise ValueError('Fill all host-settings fields before adoption')
    for key in ('project', 'app_container', 'mcp_container'):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', settings[key]):
            raise ValueError('Invalid container/project identifier')
    if settings['app_container'] == settings['mcp_container']:
        raise ValueError('Application and gateway names must differ')
    if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', settings['app_user']):
        raise ValueError('app_user must be a non-root numeric uid:gid')
    for key in ('app_port', 'mcp_port'):
        if type(settings.get(key)) is not int or not 1 <= settings[key] <= 65535:
            raise ValueError('Invalid ' + key)
    if settings['app_port'] == settings['mcp_port']:
        raise ValueError('Application and gateway ports must differ')
    for key in ('data_dir', 'models_dir', 'backup_dir', 'nginx_config', 'nginx_include', 'docker_socket'):
        value = Path(settings[key])
        mapped = host_path(value)
        if not value.is_absolute() or mapped.resolve() != mapped or value == Path('/'):
            raise ValueError('Host paths must be absolute, normalized and not symlinks')
    directories = [Path(settings[key]) for key in ('data_dir', 'models_dir', 'backup_dir')]
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(directories) for b in directories[i+1:]):
        raise ValueError('Data/models/backups must use separate, non-overlapping directories')
    include = settings['nginx_include']
    if not re.fullmatch(r'/[a-zA-Z0-9_./-]+', include) or include == settings['nginx_config']:
        raise ValueError('Use a distinct, plain nginx_include path')
    if not settings['public_origin'].startswith(('http://', 'https://')) or settings['public_origin'].endswith('/'):
        raise ValueError('public_origin must be an HTTP(S) origin without a trailing slash')
    if settings['public_host'] not in settings['mcp_allowed_hosts'].split(',') or '*' in settings['mcp_allowed_hosts']:
        raise ValueError('mcp_allowed_hosts must explicitly include public_host; no wildcards')
    if any('REPLACE' in str(value) or '/path/to/' in str(value) for value in settings.values()):
        raise ValueError('Replace example placeholders with reviewed deployment values')


def confirm_app(settings, snapshot):
    config_for_app(snapshot, snapshot['Image'])
    if snapshot['Config'].get('User') != settings['app_user']:
        raise ValueError('Adoption must preserve the existing application uid:gid')
    mounts = {item['Destination']: item for item in snapshot.get('Mounts', [])}
    for target, key in (('/data', 'data_dir'), ('/models', 'models_dir')):
        if target not in mounts or mounts[target]['Source'] != settings[key]:
            raise ValueError('Application mount does not match ' + key)
    if not mounts['/data']['RW'] or mounts['/models']['RW']:
        raise ValueError('Expected writable /data and read-only /models')
    if not snapshot['State']['Running']:
        raise ValueError('Adoption requires a running application')


def load_images(settings, bundle, release, engine):
    engine.load(bundle / 'images.tar')
    for key in ('app_image', 'mcp_image'):
        expected = release['image_ids'][release[key]]
        image = engine.request('GET', '/images/' + quote(release[key], safe='') + '/json')
        labels = image['Config'].get('Labels') or {}
        if (image['Id'] != expected or labels.get('org.opencontainers.image.version') != release['version']
                or labels.get('org.opencontainers.image.revision') != release['revision']):
            raise ValueError('Application/MCP image identity or release mismatch')


def ready(settings, version):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open('http://127.0.0.1:{}/health'.format(settings['app_port']), timeout=3) as response:
                if json.load(response).get('status') != 'ok':
                    raise ValueError('Application is not healthy')
            common.check_mcp(settings['public_origin'], version, settings['public_host'])
            return
        except (OSError, ValueError):
            time.sleep(1)
    raise ValueError('Application/MCP/proxy readiness failed; inspect pending state and rollback')


def adopt(settings, engine, state):
    app = engine.inspect(settings['app_container'])
    confirm_app(settings, app)
    containers = engine.request('GET', '/containers/json?all=1')
    if any('/' + settings['mcp_container'] in item['Names'] for item in containers):
        raise ValueError('Gateway name already exists; refusing to adopt an unknown container')
    record = {'mode': 'host', 'settings': settings, 'app': app, 'gateway': None, 'release': None}
    common.save(state / 'active.json', record)
    return record


def review_candidate(settings, bundle, active, engine, nginx_candidate=None):
    release = common.verify(bundle)
    if not release.get('mcp_image') or release['data_schema'] != settings['data_schema']:
        raise ValueError('Need a paired MCP bundle with the same reviewed data_schema')
    if active['settings'] != settings:
        raise ValueError('Host settings changed; explicit migration required')
    for field, name in (('app', settings['app_container']), ('gateway', settings['mcp_container'])):
        if active[field] and engine.inspect(name)['Id'] != active[field]['Id']:
            raise ValueError('Managed container identity changed: ' + field)
    # Inspect before stopping; refuse an occupied gateway port on first activation.
    if not active['gateway']:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', settings['mcp_port']))
    config = (nginx_candidate or host_path(settings['nginx_config'])).read_text()
    if 'include ' + settings['nginx_include'] + ';' not in config:
        raise ValueError('Review a candidate Nginx config with the managed include inside the Insight server block')
    data = host_path(settings['data_dir'])
    size = sum(path.stat().st_size for path in data.rglob('*') if path.is_file())
    backup = host_path(settings['backup_dir'])
    while not backup.exists():
        backup = backup.parent
    if shutil.disk_usage(backup).free < size + 1024**3:
        raise ValueError('Insufficient disk for a full backup and working space')
    return release, config


def prepare(settings, bundle, active, engine, state, nginx_candidate=None):
    release, config = review_candidate(settings, bundle, active, engine, nginx_candidate)
    load_images(settings, bundle, release, engine)
    folder = host_path(settings['backup_dir']) / ('host-' + str(time.time_ns()))
    folder.mkdir(parents=True, mode=0o700)
    pending = {'previous': active, 'bundle': str(bundle), 'bundle_checksum': common.sha(bundle / 'checksums.json'),
               'release': release, 'backup': str(folder), 'nginx_config': file_snapshot(settings['nginx_config']),
               'nginx_include': file_snapshot(settings['nginx_include']),
               'candidate_config': base64.b64encode(config.encode()).decode(), 'created': {}, 'phase': 'prepared'}
    common.save(state / 'pending.json', pending)
    for field in ('gateway', 'app'):
        if active[field]:
            engine.action(active[field]['Id'], 'stop?t=120')
    # Full backup only after both readers and writers stop; no database migration.
    with tarfile.open(folder / 'data.tar', 'w') as archive:
        archive.add(host_path(settings['data_dir']), arcname='data')
    (folder / 'data.tar').chmod(0o600)
    pending['data_sha256'] = common.sha(folder / 'data.tar')
    pending['phase'] = 'backed-up'
    common.save(folder / 'record.json', pending)
    common.save(state / 'pending.json', pending)
    return pending


def activate(settings, pending, engine, state):
    if pending['phase'] not in ('backed-up', 'activating'):
        raise ValueError('Backup is incomplete; rollback the runtime before retrying')
    bundle = Path(pending['bundle'])
    common.verify(bundle)
    if common.sha(bundle / 'checksums.json') != pending['bundle_checksum']:
        raise ValueError('Pending bundle changed')
    previous = pending['previous']
    pending['phase'] = 'activating'
    common.save(state / 'pending.json', pending)
    for field, name in (('app', settings['app_container']), ('gateway', settings['mcp_container'])):
        old = previous[field]
        if old:
            # Stable names are kept for the new pair; old containers remain for rollback.
            current = engine.inspect(old['Id'])
            if current['Name'] == '/' + name:
                engine.action(old['Id'], 'rename?name=' + quote(name + '-rollback-' + old['Id'][:12]))
        if field not in pending['created']:
            if field == 'app':
                config, host = config_for_app(old, pending['release']['image_ids'][pending['release']['app_image']])
                config['Env'] = [entry for entry in config.get('Env', []) if not entry.startswith('INSIGHT_MCP_HEALTH_URL=')]
                config['Env'].append('INSIGHT_MCP_HEALTH_URL=http://127.0.0.1:{}/health'.format(settings['mcp_port']))
            else:
                _, config, host = gateway_spec(settings, pending['release'])
            # Remember ownership before create so interrupted requests can be recovered.
            config.setdefault('Labels', {})['io.insight.deployment'] = settings['project']
            config['Labels']['io.insight.activation'] = Path(pending['backup']).name
            containers = engine.request('GET', '/containers/json?all=1')
            found = next((item for item in containers if '/' + name in item['Names']), None)
            if found:
                owned = engine.inspect(found['Id'])
                if owned['Config'].get('Labels', {}).get('io.insight.activation') != Path(pending['backup']).name:
                    raise ValueError('Container name occupied by an unrelated deployment')
                container_id = owned['Id']
            else:
                container_id = engine.create(name, config, host)
            pending['created'][field] = container_id
            common.save(state / 'pending.json', pending)
        engine.action(pending['created'][field], 'start')
    # Only the reviewed main config and our own include are changed; never edit other servers.
    restore_file(settings['nginx_include'], base64.b64encode(proxy_text(settings['mcp_port']).encode()).decode())
    restore_file(settings['nginx_config'], pending['candidate_config'])
    try:
        nginx(settings, 'test')
        nginx(settings, 'reload')
        ready(settings, pending['release']['version'])
    except Exception:
        restore_file(settings['nginx_config'], pending['nginx_config'])
        restore_file(settings['nginx_include'], pending['nginx_include'])
        nginx(settings, 'test')
        nginx(settings, 'reload')
        raise
    record = {'mode': 'host', 'settings': settings, 'bundle': pending['bundle'],
              'bundle_checksum': pending['bundle_checksum'], 'release': pending['release'],
              'app': engine.inspect(pending['created']['app']), 'gateway': engine.inspect(pending['created']['gateway'])}
    common.save(Path(pending['backup']) / 'activation.json', pending)
    common.save(state / 'active.json', record)
    (state / 'pending.json').unlink()
    return record


def rollback(settings, pending, engine, state, restore_data=False):
    if pending['previous']['settings'] != settings:
        raise ValueError('Rollback settings do not match the original deployment')
    if restore_data:
        archive_path = Path(pending['backup']) / 'data.tar'
        if not pending.get('data_sha256') or common.sha(archive_path) != pending['data_sha256']:
            raise ValueError('No verified complete data backup')
        if shutil.disk_usage(host_path(settings['data_dir']).parent).free < archive_path.stat().st_size + 1024**3:
            raise ValueError('Insufficient space to restore while preserving newer data')
    # Recover creates interrupted before the returned ID was saved to pending state.
    for field, name in (('app', settings['app_container']), ('gateway', settings['mcp_container'])):
        if field not in pending['created']:
            containers = engine.request('GET', '/containers/json?all=1')
            found = next((item for item in containers if '/' + name in item['Names']), None)
            if found:
                value = engine.inspect(found['Id'])
                if value['Config'].get('Labels', {}).get('io.insight.activation') == Path(pending['backup']).name:
                    pending['created'][field] = value['Id']
    for field, container_id in pending['created'].items():
        current = engine.inspect(container_id)
        if current['Config'].get('Labels', {}).get('io.insight.activation') != Path(pending['backup']).name:
            raise ValueError('Rollback refuses a container owned by another activation')
        engine.action(container_id, 'stop?t=120')
        failed_name = settings['app_container' if field == 'app' else 'mcp_container'] + '-failed-' + container_id[:12]
        if current['Name'] != '/' + failed_name:
            engine.action(container_id, 'rename?name=' + quote(failed_name))
    if restore_data:
        data = host_path(settings['data_dir'])
        target = data.with_name(data.name + '.restore-' + str(time.time_ns()))
        target.mkdir(mode=0o700)
        with tarfile.open(archive_path) as archive:
            for member in archive.getmembers():
                dest = (target / member.name).resolve()
                if target not in dest.parents or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe data backup member')
            archive.extractall(target)
        data.rename(data.with_name(data.name + '.before-rollback-' + str(time.time_ns())))
        (target / 'data').rename(data)
        target.rmdir()
    restore_file(settings['nginx_config'], pending['nginx_config'])
    restore_file(settings['nginx_include'], pending['nginx_include'])
    nginx(settings, 'test')
    for field, name in (('app', settings['app_container']), ('gateway', settings['mcp_container'])):
        old = pending['previous'][field]
        if old:
            if engine.inspect(old['Id'])['Name'] != '/' + name:
                engine.action(old['Id'], 'rename?name=' + quote(name))
            engine.action(old['Id'], 'start')
    nginx(settings, 'reload')
    common.save(state / 'active.json', pending['previous'])
    (state / 'pending.json').unlink(missing_ok=True)


def main(args, settings):
    validate(settings)
    state = args.state.resolve()
    paths = [host_path(settings[key]) for key in ('data_dir', 'models_dir', 'backup_dir')]
    for other in paths:
        for path in (state, args.bundle.resolve() if args.bundle else state):
            if path == other or path in other.parents or other in path.parents:
                raise ValueError('Bundle/state must not overlap data/models/backups')
    engine = Engine(str(host_path(settings['docker_socket'])))
    active_file = state / 'active.json'
    active = json.loads(active_file.read_text()) if active_file.exists() else None
    if args.action in ('check', 'status', 'logs'):
        if args.action == 'check':
            confirm_app(settings, engine.inspect(settings['app_container']))
            if args.bundle:
                if not active:
                    raise ValueError('Adopt the existing app before reviewing a paired upgrade')
                review_candidate(settings, args.bundle.resolve(), active, engine, args.nginx_candidate)
            print('Host preflight: current app preserved; paired bundle and reviewed Nginx include required before activation.')
        elif args.action == 'status':
            containers = engine.request('GET', '/containers/json?all=1')
            for name in (settings['app_container'], settings['mcp_container']):
                if not any('/' + name in value['Names'] for value in containers):
                    print(name, 'not deployed')
                    continue
                value = engine.inspect(name)
                print(name, value['State']['Status'], value['Image'])
        else:
            print(engine.logs(settings['mcp_container']))
        return
    if not args.apply or os.geteuid() != 0:
        raise ValueError('Host mutations require root and --apply after reviewing the plan')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    os.umask(0o077)
    with (state / 'operation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending_file = state / 'pending.json'
        if args.action == 'adopt':
            if active or pending_file.exists():
                raise ValueError('Deployment already adopted or pending')
            adopt(settings, engine, state)
            print('Adopted existing application; no container or proxy changes.')
            return
        if not active or active['settings'] != settings:
            raise ValueError('Adopt first; subsequent settings changes require an explicit migration')
        if args.action == 'rollback':
            if pending_file.exists():
                pending = json.loads(pending_file.read_text())
            elif args.backup:
                pending = json.loads((args.backup / 'activation.json').read_text())
                if any(engine.inspect(active[field]['Id'])['Id'] != pending['created'].get(field) for field in ('app', 'gateway')):
                    raise ValueError('Backup does not belong to the active container pair')
            else:
                raise ValueError('Specify --backup for a completed activation')
            rollback(settings, pending, engine, state, args.restore_data)
        elif args.action == 'resume':
            if not pending_file.exists():
                raise ValueError('No pending activation')
            activate(settings, json.loads(pending_file.read_text()), engine, state)
        elif args.action == 'upgrade':
            if pending_file.exists():
                raise ValueError('Pending activation exists; resume or rollback first')
            pending = prepare(settings, args.bundle.resolve(), active, engine, state, args.nginx_candidate)
            activate(settings, pending, engine, state)
        else:
            raise ValueError('Host mode uses adopt then upgrade, not fresh install')
        print('Host operation completed:', args.action)
