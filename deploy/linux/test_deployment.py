"""Deployment guardrail tests; run with Python unittest on Linux."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ctl', Path(__file__).with_name('insightctl.py'))
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        (self.bundle / 'release.json').write_text(json.dumps({'architecture': 'amd64'}))
        (self.bundle / 'compose.yaml').write_text('services: {}\n')
        self.seal()

    def seal(self):
        (self.bundle / 'checksums.json').write_text(json.dumps({
            p.name: ctl.sha(p) for p in self.bundle.iterdir() if p.name != 'checksums.json'
        }))

    def test_valid_and_tampered_bundle(self):
        self.assertEqual(ctl.verify(self.bundle)['architecture'], 'amd64')
        (self.bundle / 'compose.yaml').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            ctl.verify(self.bundle)

    def test_extra_file_rejected(self):
        (self.bundle / '.env').write_text('unexpected local config')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            ctl.verify(self.bundle)

    def test_model_mismatch_rejected(self):
        record = {'env': {'MODELS_DIR': str(self.root)}, 'release': {'model_checksums': {'missing.bin': 'abc'}}}
        with self.assertRaisesRegex(ValueError, 'Model missing'):
            ctl.verify_models(record)

    def test_host_route_overlap_rejected(self):
        settings = dict(project='test', app_uid=1002, app_gid=1002, data_dir=str(self.root / 'data'),
                        models_dir=str(self.root / 'models'), backup_dir=str(self.root / 'backups'),
                        network_subnet='172.19.211.0/24', web_ip='172.19.211.10')
        def fake_output(args):
            if args[:2] == ['ip', '-j']:
                return json.dumps([{'dst': '172.19.0.0/16', 'dev': 'eth0'}])
            if args[:3] == ['docker', 'network', 'ls']:
                return ''
            return '24.0.9'
        with patch.object(ctl, 'output', side_effect=fake_output):
            with self.assertRaisesRegex(ValueError, 'overlaps host route'):
                ctl.preflight(settings, self.bundle)

    def test_existing_other_project_network_rejected(self):
        settings = dict(project='test', app_uid=1002, app_gid=1002, data_dir=str(self.root / 'data'),
                        models_dir=str(self.root / 'models'), backup_dir=str(self.root / 'backups'),
                        network_subnet='192.168.243.0/24', web_ip='192.168.243.10')
        def fake_output(args):
            if args[:3] == ['docker', 'network', 'ls']:
                return 'other'
            if args[:3] == ['docker', 'network', 'inspect']:
                return json.dumps([{'Name': 'other', 'Labels': {}, 'IPAM': {'Config': [{'Subnet': '192.168.243.0/24'}]}}])
            return '24.0.9'
        with patch.object(ctl, 'output', side_effect=fake_output):
            with self.assertRaisesRegex(ValueError, 'overlaps Docker network'):
                ctl.preflight(settings, self.bundle)

    def test_backup_stops_before_copy_and_preserves_bytes(self):
        data = self.root / 'data'
        data.mkdir()
        (data / 'credentials.key').write_bytes(b'not-a-real-secret')
        state = self.root / 'state'
        state.mkdir()
        with patch.object(ctl, 'compose') as compose:
            folder = ctl.backup({'project': 'test'}, {'data_dir': str(data), 'backup_dir': str(self.root / 'backups')}, state)
            compose.assert_called_once_with({'project': 'test'}, 'stop', '--timeout', '120')
        self.assertEqual(json.loads((folder / 'record.json').read_text())['data_sha256'], ctl.sha(folder / 'data.tar'))
        self.assertEqual((folder / 'data.tar').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
