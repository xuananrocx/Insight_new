"""Host takeover/upgrade transaction tests, using only disposable files and a fake daemon."""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostctl as host  # noqa: E402
import insightctl as ctl  # noqa: E402

spec = importlib.util.spec_from_file_location('package', Path(__file__).with_name('package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class FakeEngine:
    def __init__(self, settings, release):
        self.release = release
        self.events = []
        self.containers = {'old-app': {
            'Id': 'old-app', 'Image': 'sha256:old', 'Name': '/' + settings['app_container'],
            'State': {'Running': True, 'Status': 'running'},
            'Config': {'Image': 'old-image', 'User': settings['app_user'], 'Env': ['TEST_PRIVATE=dummy'],
                       'Cmd': ['python', '-m', 'uvicorn', 'src.api.main:app', '--port', '8888'],
                       'Labels': {'original-label': 'preserved'}},
            'HostConfig': {'NetworkMode': 'host', 'RestartPolicy': {'Name': 'unless-stopped'},
                           'Binds': [settings['data_dir'] + ':/data', settings['models_dir'] + ':/models:ro'],
                           'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '20m'}}},
            'Mounts': [{'Source': settings[key], 'Destination': target, 'RW': writable}
                       for target, key, writable in [('/data', 'data_dir', True), ('/models', 'models_dir', False)]],
        }}

    def inspect(self, name):
        value = self.containers.get(name) or next((v for v in self.containers.values() if v['Name'] == '/' + name), None)
        if value is None:
            raise ValueError('Missing container ' + name)
        return copy.deepcopy(value)

    def request(self, method, path, body=None):
        if path == '/containers/json?all=1':
            return [{'Id': v['Id'], 'Names': [v['Name']]} for v in self.containers.values()]
        if path.startswith('/images/'):
            image = unquote(path[len('/images/'):-len('/json')])
            return {'Id': self.release['image_ids'][image], 'Config': {'Labels': {
                'org.opencontainers.image.version': self.release['version'],
                'org.opencontainers.image.revision': self.release['revision']}}}
        raise AssertionError((method, path))

    def action(self, name, action):
        self.events.append((name, action))
        current = self.containers[self.inspect(name)['Id']]
        if action.startswith('rename?name='):
            new = '/' + unquote(action.split('=', 1)[1])
            if any(v['Name'] == new and v['Id'] != current['Id'] for v in self.containers.values()):
                raise ValueError('Name occupied')
            current['Name'] = new
        elif action.startswith('stop'):
            current['State'] = {'Running': False, 'Status': 'exited'}
        elif action == 'start':
            current['State'] = {'Running': True, 'Status': 'running'}
        else:
            raise AssertionError(action)

    def create(self, name, config, host_config):
        if any(v['Name'] == '/' + name for v in self.containers.values()):
            raise ValueError('Name occupied')
        cid = 'new-' + str(len(self.containers))
        self.events.append((cid, 'create'))
        self.containers[cid] = {'Id': cid, 'Name': '/' + name, 'Image': config['Image'],
                                'Config': copy.deepcopy(config), 'HostConfig': copy.deepcopy(host_config),
                                'State': {'Running': False, 'Status': 'created'}}
        return cid

    def load(self, archive):
        self.events.append(('images', 'load'))


class HostTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings = json.loads(Path(__file__).with_name('host-settings.example.json').read_text())
        for key in ('data_dir', 'models_dir', 'backup_dir'):
            self.settings[key] = str(self.root / key)
        for key in ('nginx_config', 'nginx_include'):
            self.settings[key] = str(self.root / (key + '.conf'))
        self.settings['nginx_server_config'] = self.settings['nginx_config']
        self.settings.update(nginx_binary='/usr/sbin/nginx', nginx_prefix=str(self.root),
                             docker_socket=str(self.root / 'docker.sock'), public_host='kb.test:8088',
                             mcp_allowed_hosts='kb.test:8088,127.0.0.1:8088')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.settings['mcp_port'] = probe.getsockname()[1]
        Path(self.settings['data_dir']).mkdir()
        Path(self.settings['models_dir']).mkdir()
        (Path(self.settings['data_dir']) / 'credentials.key').write_bytes(b'isolated dummy fixture')
        Path(self.settings['nginx_config']).write_text('events {}\nhttp { server { listen 8088; location / { proxy_pass http://127.0.0.1:8888; } } }\n')
        self.original_proxy = Path(self.settings['nginx_config']).read_bytes()
        self.candidate = self.root / 'candidate.conf'
        self.candidate.write_text('events {}\nhttp { server { listen 8088; include ' + self.settings['nginx_include'] + '; } }\n')
        self.state = self.root / 'state'
        self.state.mkdir()
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        (self.bundle / 'checksums.json').write_text('{}')
        (self.bundle / 'images.tar').write_bytes(b'fixture image archive')
        self.release = dict(format=2, version='0.1.9', revision='a' * 40, data_schema='insight-0.1.1',
                            app_image='app:fixed', mcp_image='mcp:fixed', web_image='web:fixed',
                            image_ids={'app:fixed': 'sha256:app', 'mcp:fixed': 'sha256:mcp', 'web:fixed': 'sha256:web'})
        self.engine = FakeEngine(self.settings, self.release)
        self.verify = patch.object(ctl, 'verify', return_value=self.release)
        self.verify.start()
        self.addCleanup(self.verify.stop)
        self.nginx = patch.object(host, 'nginx')
        self.nginx_mock = self.nginx.start()
        self.addCleanup(self.nginx.stop)
        self.ready = patch.object(host, 'ready')
        self.ready_mock = self.ready.start()
        self.addCleanup(self.ready.stop)
        self.active = host.adopt(self.settings, self.engine, self.state)

    def prepare(self):
        return host.prepare(self.settings, self.bundle, self.active, self.engine, self.state, self.candidate)

    def test_adoption_has_no_service_mutation_and_rejects_unknown_gateway(self):
        host.validate(self.settings)
        self.assertEqual(self.engine.events, [])
        self.assertEqual((self.state / 'active.json').stat().st_mode & 0o777, 0o600)
        name, config, host_config = host.gateway_spec(self.settings, self.release)
        self.assertNotIn('Binds', host_config)
        self.assertIn('INSIGHT_MCP_BIND=127.0.0.1', config['Env'])
        self.engine.create(name, config, host_config)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            host.adopt(self.settings, self.engine, self.state)

    def test_first_activation_preserves_runtime_and_next_upgrade_manages_both(self):
        original = copy.deepcopy(self.active['app'])
        pending = self.prepare()
        self.assertFalse(self.engine.inspect('old-app')['State']['Running'])
        self.assertTrue((Path(pending['backup']) / 'data.tar').exists())
        record = host.activate(self.settings, pending, self.engine, self.state)
        self.assertEqual(record['app']['Config']['Env'][:-1], original['Config']['Env'])
        self.assertTrue(record['app']['Config']['Env'][-1].startswith('INSIGHT_MCP_HEALTH_URL='))
        self.assertEqual(record['app']['Config']['Cmd'], original['Config']['Cmd'])
        self.assertEqual(record['app']['HostConfig'], original['HostConfig'])
        self.assertEqual(self.active['app'], original)
        self.assertTrue(self.engine.inspect('old-app')['Name'].startswith('/insight-rollback-'))
        self.assertIn('location = /mcp', Path(self.settings['nginx_include']).read_text())
        self.assertFalse((self.state / 'pending.json').exists())
        # A second upgrade takes the managed pair through exactly the same transaction.
        self.active = record
        pending2 = self.prepare()
        self.assertFalse(self.engine.inspect(record['gateway']['Id'])['State']['Running'])
        record2 = host.activate(self.settings, pending2, self.engine, self.state)
        self.assertNotEqual(record2['gateway']['Id'], record['gateway']['Id'])
        host.rollback(self.settings, pending2, self.engine, self.state)
        self.assertEqual(self.engine.inspect('insight')['Id'], record['app']['Id'])
        self.assertEqual(self.engine.inspect('insight-mcp')['Id'], record['gateway']['Id'])

    def test_legacy_writable_models_and_separate_server_file_are_preserved(self):
        self.engine.containers['old-app']['Mounts'][1]['RW'] = True
        self.engine.containers['old-app']['HostConfig']['Binds'][1] = self.settings['models_dir'] + ':/models'
        self.settings['nginx_server_config'] = str(self.root / 'insight-server.conf')
        Path(self.settings['nginx_server_config']).write_bytes(self.original_proxy)
        self.active = host.adopt(self.settings, self.engine, self.state)
        host.confirm_app(self.settings, self.active['app'])
        pending = self.prepare()
        record = host.activate(self.settings, pending, self.engine, self.state)
        self.assertEqual(record['app']['HostConfig']['Binds'][1], self.settings['models_dir'] + ':/models')
        self.assertEqual(Path(self.settings['nginx_config']).read_bytes(), self.original_proxy)
        self.assertNotEqual(Path(self.settings['nginx_server_config']).read_bytes(), self.original_proxy)
        host.rollback(self.settings, pending, self.engine, self.state)
        self.assertEqual(Path(self.settings['nginx_server_config']).read_bytes(), self.original_proxy)

    def test_nginx_failure_restores_proxy_and_allows_runtime_rollback(self):
        pending = self.prepare()
        self.nginx_mock.side_effect = [ValueError('bad nginx candidate'), None, None]
        with self.assertRaisesRegex(ValueError, 'bad nginx'):
            host.activate(self.settings, pending, self.engine, self.state)
        self.assertEqual(Path(self.settings['nginx_config']).read_bytes(), self.original_proxy)
        self.assertFalse(Path(self.settings['nginx_include']).exists())
        self.assertTrue((self.state / 'pending.json').exists())
        self.nginx_mock.side_effect = None
        host.rollback(self.settings, pending, self.engine, self.state)
        self.assertTrue(self.engine.inspect('old-app')['State']['Running'])
        self.assertEqual(self.engine.inspect('insight')['Id'], 'old-app')

    def test_gateway_start_failure_retains_pending_and_restores_original_pair(self):
        pending = self.prepare()
        action = self.engine.action
        def fail_gateway(name, operation):
            if operation == 'start' and name == pending['created'].get('gateway'):
                raise ValueError('gateway start failure')
            return action(name, operation)
        with patch.object(self.engine, 'action', side_effect=fail_gateway):
            with self.assertRaisesRegex(ValueError, 'gateway start failure'):
                host.activate(self.settings, pending, self.engine, self.state)
        host.rollback(self.settings, pending, self.engine, self.state)
        self.assertTrue(self.engine.inspect('old-app')['State']['Running'])
        self.assertEqual(Path(self.settings['nginx_config']).read_bytes(), self.original_proxy)

    def test_backup_failure_never_starts_new_app_and_can_restore_runtime(self):
        with patch.object(host.tarfile, 'open', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                self.prepare()
        pending = json.loads((self.state / 'pending.json').read_text())
        self.assertEqual(pending['phase'], 'prepared')
        self.assertEqual(len(self.engine.containers), 1)
        with self.assertRaisesRegex(ValueError, 'Backup is incomplete'):
            host.activate(self.settings, pending, self.engine, self.state)
        host.rollback(self.settings, pending, self.engine, self.state)
        self.assertTrue(self.engine.inspect('old-app')['State']['Running'])

    def test_interrupted_create_can_resume_without_duplicate_container(self):
        pending = self.prepare()
        create = self.engine.create
        def lost_response(*args):
            create(*args)
            raise OSError('response lost')
        with patch.object(self.engine, 'create', side_effect=lost_response):
            with self.assertRaises(OSError):
                host.activate(self.settings, pending, self.engine, self.state)
        self.assertEqual(len(self.engine.containers), 2)
        saved = json.loads((self.state / 'pending.json').read_text())
        host.activate(self.settings, saved, self.engine, self.state)
        self.assertEqual(len(self.engine.containers), 3)

    def test_interrupted_create_can_rollback_without_a_saved_new_id(self):
        pending = self.prepare()
        create = self.engine.create
        def lost_response(*args):
            create(*args)
            raise OSError('response lost')
        with patch.object(self.engine, 'create', side_effect=lost_response), self.assertRaises(OSError):
            host.activate(self.settings, pending, self.engine, self.state)
        saved = json.loads((self.state / 'pending.json').read_text())
        host.rollback(self.settings, saved, self.engine, self.state)
        self.assertEqual(self.engine.inspect('insight')['Id'], 'old-app')

    def test_restore_keeps_newer_data_and_runtime_rollback_does_not_replace_data(self):
        pending = self.prepare()
        host.activate(self.settings, pending, self.engine, self.state)
        data = Path(self.settings['data_dir'])
        (data / 'new-write.txt').write_text('newer data')
        host.rollback(self.settings, pending, self.engine, self.state, restore_data=True)
        self.assertEqual((data / 'credentials.key').read_bytes(), b'isolated dummy fixture')
        self.assertFalse((data / 'new-write.txt').exists())
        preserved = list(data.parent.glob(data.name + '.before-rollback-*'))
        self.assertEqual((preserved[0] / 'new-write.txt').read_text(), 'newer data')

    def test_preflight_refuses_missing_include_changed_identity_and_schema(self):
        for change, message in [('include', 'candidate Nginx'), ('identity', 'identity changed'), ('schema', 'data_schema')]:
            with self.subTest(change=change):
                candidate = self.candidate
                if change == 'include':
                    candidate = None
                elif change == 'identity':
                    self.engine.containers['old-app']['Id'] = 'changed'
                else:
                    self.release['data_schema'] = 'changed'
                with self.assertRaisesRegex(ValueError, message):
                    host.prepare(self.settings, self.bundle, self.active, self.engine, self.state, candidate)
                self.engine.containers['old-app']['Id'] = 'old-app'
                self.assertEqual(self.engine.events, [])
                self.assertTrue(self.engine.inspect('old-app')['State']['Running'])

    def test_tampered_backup_rejected_before_stopping_active_pair(self):
        pending = self.prepare()
        record = host.activate(self.settings, pending, self.engine, self.state)
        (Path(pending['backup']) / 'data.tar').write_bytes(b'tampered')
        events = len(self.engine.events)
        with self.assertRaisesRegex(ValueError, 'verified complete data backup'):
            host.rollback(self.settings, pending, self.engine, self.state, restore_data=True)
        self.assertEqual(len(self.engine.events), events)
        self.assertTrue(self.engine.inspect(record['app']['Id'])['State']['Running'])

    def test_pending_bundle_tampering_refused(self):
        pending = self.prepare()
        (self.bundle / 'checksums.json').write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'Pending bundle changed'):
            host.activate(self.settings, pending, self.engine, self.state)
        host.rollback(self.settings, pending, self.engine, self.state)
        self.assertTrue(self.engine.inspect('old-app')['State']['Running'])


class ImagePairing(unittest.TestCase):
    def test_bundle_rejects_mismatched_gateway_or_wrong_platform(self):
        labels = {'org.opencontainers.image.version': '0.1.9', 'org.opencontainers.image.revision': 'a' * 40}
        app = {'Architecture': 'amd64', 'Os': 'linux', 'Config': {'Labels': labels}}
        gateway = copy.deepcopy(app)
        gateway['Config']['Labels']['io.insight.component'] = 'mcp'
        self.assertEqual(package.validate_images(app, app, gateway), ('0.1.9', 'a' * 40))
        for key, value in [('org.opencontainers.image.version', '0.1.8'),
                           ('org.opencontainers.image.revision', 'b' * 40), ('io.insight.component', 'app')]:
            wrong = copy.deepcopy(gateway)
            wrong['Config']['Labels'][key] = value
            with self.assertRaisesRegex(ValueError, 'must match'):
                package.validate_images(app, app, wrong)
        gateway['Architecture'] = 'arm64'
        with self.assertRaisesRegex(ValueError, 'linux/amd64'):
            package.validate_images(app, app, gateway)


if __name__ == '__main__':
    unittest.main()
