#!/usr/bin/env python3
"""Offline deployment controller; Python 3.8+, Docker and Compose v2 required."""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import urllib.error


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return subprocess.check_output(args, text=True).strip()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify(bundle):
    manifest = json.loads((bundle / 'release.json').read_text())
    checks = json.loads((bundle / 'checksums.json').read_text())
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(checks) | {'checksums.json'}:
        raise ValueError('Bundle has missing or unlisted files')
    for name, digest in checks.items():
        path = bundle / name
        if path.is_symlink() or bundle not in path.resolve().parents or sha(path) != digest:
            raise ValueError('Bundle checksum/path error: ' + name)
    if manifest['architecture'] != 'amd64':
        raise ValueError('This controller currently supports linux/amd64 only')
    return manifest


def compose(record, *args):
    env = os.environ.copy()
    env.update({key: str(value) for key, value in record['env'].items()})
    return run(['docker', 'compose', '--project-name', record['project'],
                '--project-directory', record['bundle'], '-f',
                str(Path(record['bundle']) / 'compose.yaml'), *args], env=env)


def record_for(settings, bundle, release):
    mapping = {'app_uid': 'APP_UID', 'app_gid': 'APP_GID', 'data_dir': 'DATA_DIR',
               'models_dir': 'MODELS_DIR', 'bind_address': 'BIND_ADDRESS', 'http_port': 'HTTP_PORT',
               'network_subnet': 'NETWORK_SUBNET', 'web_ip': 'WEB_IP', 'app_memory': 'APP_MEMORY',
               'app_memory_swap': 'APP_MEMORY_SWAP', 'app_cpus': 'APP_CPUS', 'model_threads': 'MODEL_THREADS'}
    env = {dest: settings[src] for src, dest in mapping.items()}
    env.update(APP_IMAGE=release['app_image'], WEB_IMAGE=release['web_image'])
    return dict(bundle=str(bundle), project=settings['project'], env=env, release=release,
                bundle_checksum=sha(bundle / 'checksums.json'))


def preflight(settings, bundle, active=None):
    release = verify(bundle)
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', settings['project']):
        raise ValueError('Invalid Compose project name')
    if any(not isinstance(settings.get(key), int) or settings[key] <= 0 for key in ('app_uid', 'app_gid')):
        raise ValueError('APP_UID and APP_GID must identify a non-root deployment user')
    if os.uname().machine not in ('x86_64', 'amd64'):
        raise ValueError('Host architecture must be x86_64')
    output(['docker', 'info', '--format', '{{.ServerVersion}}'])
    output(['docker', 'compose', 'version'])
    folders = [Path(settings[k]) for k in ('data_dir', 'models_dir', 'backup_dir')]
    for folder in folders:
        if not folder.is_absolute() or folder == Path('/') or folder.resolve() != folder:
            raise ValueError('Paths must be absolute, normalized and not symlinks')
    for a in folders:
        for b in folders:
            if a != b and a in b.parents:
                raise ValueError('Data, model and backup directories must not contain one another')
    if len(set(folders)) != 3:
        raise ValueError('Data, models and backups must use separate directories')
    subnet = ipaddress.ip_network(settings['network_subnet'], strict=True)
    web = ipaddress.ip_address(settings['web_ip'])
    if subnet.version != 4 or not subnet.is_private or subnet.prefixlen < 24 or subnet.prefixlen > 28:
        raise ValueError('Choose an unused private IPv4 /24 through /28 subnet')
    if web not in subnet or web in (subnet.network_address, subnet.broadcast_address, subnet.network_address + 1):
        raise ValueError('WEB_IP must be usable and must not be the gateway')
    existing = output(['docker', 'network', 'ls', '-q']).split()
    owned_subnets = set()
    for network in existing:
        info = json.loads(output(['docker', 'network', 'inspect', network]))[0]
        owner = (info.get('Labels') or {}).get('com.docker.compose.project')
        for config in info.get('IPAM', {}).get('Config') or []:
            value = config.get('Subnet')
            if not value:
                continue
            other = ipaddress.ip_network(value)
            if other.version == 4 and subnet.overlaps(other):
                if active and owner == settings['project'] and other == subnet:
                    owned_subnets.add(str(other))
                else:
                    raise ValueError('Subnet overlaps Docker network ' + info['Name'])
    routes = json.loads(output(['ip', '-j', '-4', 'route', 'show', 'table', 'all']))
    for route in routes:
        destination = route.get('dst', 'default')
        if destination == 'default':
            continue
        network = ipaddress.ip_network(destination, strict=False)
        if subnet.overlaps(network) and str(network) not in owned_subnets:
            # Local/broadcast routes of our own Docker bridge are also expected.
            if active and route.get('dev', '').startswith('br-') and any(network.subnet_of(ipaddress.ip_network(s)) for s in owned_subnets):
                continue
            raise ValueError('Subnet overlaps host route: ' + destination)
    if active:
        if active['project'] != settings['project']:
            raise ValueError('Changing project during upgrade is not supported')
        for key in ('DATA_DIR', 'MODELS_DIR', 'NETWORK_SUBNET', 'WEB_IP', 'BIND_ADDRESS', 'HTTP_PORT', 'APP_UID', 'APP_GID'):
            candidate = record_for(settings, bundle, release)['env'][key]
            if str(active['env'][key]) != str(candidate):
                raise ValueError('Upgrade must preserve ' + key + '; use an explicit migration')
    else:
        with socket.socket() as probe:
            probe.bind((settings['bind_address'], int(settings['http_port'])))
    data = folders[0]
    size = sum(p.stat().st_size for p in data.rglob('*') if p.is_file()) if data.exists() else 0
    model_size = sum(p.stat().st_size for p in (bundle / 'models').rglob('*') if p.is_file())
    docker_root = Path(output(['docker', 'info', '--format', '{{.DockerRootDir}}']))
    requirements = [(folders[2], size), (data, 1024**3),
                    (docker_root, (bundle / 'images.tar').stat().st_size), (folders[1], model_size)]
    devices = {}
    for directory, needed in requirements:
        parent = directory
        while not parent.exists():
            parent = parent.parent
        device = parent.stat().st_dev
        previous = devices.get(device, (parent, 0))
        devices[device] = (parent, previous[1] + needed)
    for parent, needed in devices.values():
        if shutil.disk_usage(parent).free < needed:
            raise ValueError('Insufficient space for data, backup, model copy and image load at ' + str(parent))
    result = record_for(settings, bundle, release)
    compose(result, 'config', '--quiet')
    print('Preflight passed. Check cloud ingress and host capacity separately; no settings changed.', flush=True)
    return result


def load_images(record):
    bundle = Path(record['bundle'])
    run(['docker', 'load', '-i', str(bundle / 'images.tar')])
    for tag, expected in record['release']['image_ids'].items():
        actual = output(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'])
        if actual != expected:
            raise ValueError('Loaded image identity mismatch: ' + tag)


def verify_models(record):
    root = Path(record['env']['MODELS_DIR'])
    for name, digest in record['release']['model_checksums'].items():
        p = root / name
        if root not in p.resolve().parents or not p.is_file() or sha(p) != digest:
            raise ValueError('Model missing or checksum mismatch: ' + name)


def start(record):
    compose(record, 'up', '-d', '--wait', '--wait-timeout', '240', '--pull', 'never')
    host = record['env']['BIND_ADDRESS']
    if host == '0.0.0.0':
        host = '127.0.0.1'
    address = 'http://{}:{}/api/v1/auth/status'.format(host, record['env']['HTTP_PORT'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(15):
        try:
            with opener.open(address, timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise ValueError('Frontend/API health check failed')


def backup(active, settings, state):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    folder = Path(settings['backup_dir']) / stamp
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    compose(active, 'stop', '--timeout', '120')
    # Never copy a live SQLite/Chroma store. Leave stopped on backup failure.
    with tarfile.open(folder / 'data.tar', 'w') as archive:
        archive.add(settings['data_dir'], arcname='data')
    (folder / 'data.tar').chmod(0o600)
    save(folder / 'record.json', dict(active=active, data_sha256=sha(folder / 'data.tar')))
    save(state / 'last-backup.json', {'path': str(folder)})
    print('Consistent backup:', folder, flush=True)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'install', 'upgrade', 'resume', 'status', 'logs', 'rollback'])
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True, help='Private deployment state directory')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--backup', type=Path, help='Explicit backup directory for rollback')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore-data', action='store_true', help='Required: restore snapshot; newer writes remain archived in displaced directory')
    args = parser.parse_args()
    if args.action in ('check', 'install', 'upgrade') and not args.bundle:
        parser.error('--bundle is required for ' + args.action)
    settings = json.loads(args.settings.read_text())
    state = args.state.resolve()
    active_file = state / 'active.json'
    active = json.loads(active_file.read_text()) if active_file.exists() else None
    if args.action in ('status', 'logs'):
        pending = state / 'pending.json'
        shown = json.loads(pending.read_text()) if pending.exists() else active
        if not shown:
            raise ValueError('No managed deployment')
        compose(shown, *(['ps', '-a'] if args.action == 'status' else ['logs', '--tail', '100']))
        return
    for key in ('data_dir', 'models_dir', 'backup_dir'):
        other = Path(settings[key]).resolve()
        if state == other or state in other.parents or other in state.parents:
            raise ValueError('State must not overlap data/models/backups')
        if args.bundle:
            bundle_path = args.bundle.resolve()
            if bundle_path == other or bundle_path in other.parents or other in bundle_path.parents:
                raise ValueError('Bundle must not overlap data/models/backups')
    if args.action == 'check':
        preflight(settings, args.bundle.resolve(), active)
        return
    if not args.apply:
        raise ValueError('Mutation requires --apply after reviewing the deployment plan')
    if os.geteuid() != 0:
        raise ValueError('Run mutation commands with sudo so data ownership and backup restore are preserved')
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.umask(0o077)
    with (state / 'operation.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'resume':
            pending_file = state / 'pending.json'
            if not pending_file.exists():
                raise ValueError('No pending activation to resume')
            pending = json.loads(pending_file.read_text())
            candidate = preflight(settings, Path(pending['bundle']), pending)
            if candidate != pending:
                raise ValueError('Pending bundle or settings changed; review migration instead of resuming')
            verify_models(candidate)
            load_images(candidate)
            start(candidate)
            save(active_file, candidate)
            pending_file.unlink()
            print('Pending activation completed')
            return
        if args.action in ('install', 'upgrade') and (state / 'pending.json').exists():
            raise ValueError('Pending activation exists; inspect logs then resume or rollback')
        if args.action == 'rollback':
            if not active or not args.backup or not args.restore_data:
                raise ValueError('Rollback requires active deployment, --backup and --restore-data')
            folder = args.backup.resolve()
            info = json.loads((folder / 'record.json').read_text())
            previous = info['active']
            if previous['env']['DATA_DIR'] != active['env']['DATA_DIR'] or previous['project'] != active['project']:
                raise ValueError('Backup does not belong to this deployment')
            verify(Path(previous['bundle']))
            if sha(Path(previous['bundle']) / 'checksums.json') != previous['bundle_checksum']:
                raise ValueError('Previous bundle changed since deployment')
            if sha(folder / 'data.tar') != info['data_sha256']:
                raise ValueError('Backup checksum mismatch')
            load_images(previous)
            verify_models(previous)
            data = Path(active['env']['DATA_DIR'])
            if shutil.disk_usage(data.parent).free < (folder / 'data.tar').stat().st_size + 1024**3:
                raise ValueError('Insufficient space to restore while preserving current data')
            restore = data.with_name(data.name + '.restore-' + str(time.time_ns()))
            restore.mkdir(mode=0o700)
            with tarfile.open(folder / 'data.tar') as archive:
                for member in archive.getmembers():
                    target = (restore / member.name).resolve()
                    if restore not in target.parents or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        raise ValueError('Unsafe backup member')
                archive.extractall(restore)
            compose(active, 'stop', '--timeout', '120')
            displaced = data.with_name(data.name + '.before-rollback-' + str(time.time_ns()))
            data.rename(displaced)
            (restore / 'data').rename(data)
            restore.rmdir()
            start(previous)
            save(active_file, previous)
            if (state / 'pending.json').exists():
                (state / 'pending.json').unlink()
            print('Rollback complete; newer data preserved at', displaced)
            return
        bundle = args.bundle.resolve()
        candidate = preflight(settings, bundle, active)
        data, models = Path(settings['data_dir']), Path(settings['models_dir'])
        if args.action == 'install':
            if active or data.exists():
                raise ValueError('Fresh install refuses an existing data directory or managed deployment')
            if candidate['release']['kind'] == 'full':
                if models.exists():
                    raise ValueError('Full install refuses an existing model directory')
                models.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(bundle / 'models', models)
                for path in [models, *models.rglob('*')]:
                    path.chmod(0o755 if path.is_dir() else 0o644)
            verify_models(candidate)
            data.mkdir(parents=True, mode=0o700)
            shutil.copyfile(bundle / 'config.local.example.yaml', data / 'config.local.yaml')
            os.chown(data, settings['app_uid'], settings['app_gid'])
            os.chown(data / 'config.local.yaml', settings['app_uid'], settings['app_gid'])
            load_images(candidate)
        else:
            if not active:
                raise ValueError('Upgrade requires an existing managed deployment; legacy installs need migration')
            if candidate['release']['data_schema'] != active['release']['data_schema']:
                raise ValueError('Schema change requires a separately reviewed migration plan')
            verify_models(candidate)
            load_images(candidate)
            backup(active, settings, state)
        save(state / 'pending.json', candidate)
        try:
            start(candidate)
        except Exception:
            print('Activation failed. Data is preserved; inspect logs and use explicit rollback. No automatic data overwrite.', file=sys.stderr)
            raise
        save(active_file, candidate)
        (state / 'pending.json').unlink()
        print('Deployment healthy:', candidate['release']['version'], candidate['release']['revision'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('ERROR:', error, file=sys.stderr)
        sys.exit(1)
