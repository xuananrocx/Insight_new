"""Release tooling tests; deliberately independent of application services."""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load('release_build')
summary = load('release_summary')
materials = load('prepare_linux_ci')
SHA = 'a' * 40


class BuildGuards(unittest.TestCase):
    def test_public_download_checks_integrity_and_removes_partial_files(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'model.bin'
            with patch.object(materials.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(b'bad')), \
                 patch.object(materials.time, 'sleep'):
                with self.assertRaises(ValueError):
                    materials.verified_download('https://example.invalid/model', destination, '0' * 64)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name('model.bin.partial').exists())
            payload = b'{}'
            expected = hashlib.sha1(b'blob 2\0' + payload).hexdigest()
            with patch.object(materials.urllib.request, 'urlopen', return_value=io.BytesIO(payload)):
                materials.verified_download('https://example.invalid/config', destination,
                                            expected, 'git-blob-sha1', 2)
            self.assertEqual(destination.read_bytes(), payload)

    def test_only_exact_clean_commit(self):
        for revision in ('main', 'v0.1.1', 'abcdef0'):
            with self.assertRaises(ValueError):
                build.verify_checkout(revision)
        for responses in (['b' * 40], [SHA, ' M src/app.py'], [SHA, '?? secret.json']):
            with patch.object(build, 'capture', side_effect=responses), self.assertRaises(ValueError):
                build.verify_checkout(SHA)
        with patch.object(build, 'capture', side_effect=[SHA, '']):
            build.verify_checkout(SHA)

    def test_model_archive_rejects_escape_and_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, kind in (('../escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)):
                archive = root / 'models.tar'
                with tarfile.open(archive, 'w') as tar:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                    tar.addfile(entry)
                with self.assertRaises(ValueError):
                    build.safe_extract(archive, root / 'out')
                self.assertFalse((root / 'out').exists())

    def test_model_archive_extracts_plain_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with tarfile.open(root / 'models.tar', 'w') as tar:
                entry = tarfile.TarInfo('model/config.json')
                entry.size = 2
                tar.addfile(entry, io.BytesIO(b'{}'))
            with patch.object(build.shutil, 'disk_usage') as disk:
                disk.return_value.free = 10 * 1024**3
                build.safe_extract(root / 'models.tar', root / 'out')
            self.assertEqual((root / 'out/model/config.json').read_bytes(), b'{}')

    def test_manifest_hashes_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifact = root / 'candidate.zip'
            artifact.write_bytes(b'candidate\r\n')
            with patch.object(build, 'capture', return_value='test'), patch.object(build, 'executable', side_effect=lambda x: x):
                result = build.write_manifest(root, '0.1.1', SHA, 'windows', {})
            self.assertEqual(result['files']['candidate.zip']['sha256'], build.digest(artifact))
            self.assertEqual(result['acceptance_status'], 'pending')
            self.assertEqual(result['publish_status'], 'not_published')
            self.assertEqual(json.loads((root / 'build-manifest.json').read_text())['revision'], SHA)
            self.assertIn(build.digest(root / 'build-manifest.json'), (root / 'SHA256SUMS').read_text())
            self.assertNotIn('SHA256SUMS', (root / 'SHA256SUMS').read_text())

    def test_mutable_image_tags_are_rejected(self):
        for reference in ('nginx:latest', 'insight:0.1.1', '', None):
            with self.assertRaises(ValueError):
                build.require_pinned_image(reference)


class SummaryGuards(unittest.TestCase):
    def test_partial_build_is_not_complete(self):
        result = {'windows': dict(platform='windows', revision=SHA, result='success')}
        report = summary.summarize(SHA, ['windows', 'linux'], result)
        self.assertFalse(report['all_selected_built'])
        self.assertEqual(report['platforms']['macos']['build_status'], 'not_selected')
        self.assertEqual(report['platforms']['linux']['build_status'], 'missing_or_wrong_revision')

    def test_success_does_not_mean_published(self):
        result = {'linux': dict(platform='linux', revision=SHA, result='success')}
        report = summary.summarize(SHA, ['linux'], result)
        self.assertTrue(report['all_selected_built'])
        self.assertFalse(report['published'])
        self.assertEqual(report['platforms']['linux']['acceptance_status'], 'pending')

    def test_failed_and_wrong_commit_are_not_complete(self):
        for revision, status in ((SHA, 'failure'), ('b' * 40, 'success'), (SHA, 'cancelled')):
            result = {'linux': dict(platform='linux', revision=revision, result=status)}
            self.assertFalse(summary.summarize(SHA, ['linux'], result)['all_selected_built'])
        self.assertFalse(summary.summarize(SHA, [], {})['all_selected_built'])


if __name__ == '__main__':
    unittest.main()
