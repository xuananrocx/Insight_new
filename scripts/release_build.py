"""Build one selected platform from a clean, fixed checkout; never publish."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def capture(args):
    return subprocess.check_output([str(a) for a in args], cwd=ROOT, text=True).strip()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_version(root=ROOT):
    for node in ast.parse((root / 'src/__init__.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
            version = ast.literal_eval(node.value)
            if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
                raise ValueError('Invalid application version')
            return version
    raise ValueError('Missing canonical version')


def verify_checkout(revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Use a full, lowercase 40-character commit SHA; branches/tags are not accepted')
    if capture(['git', 'rev-parse', 'HEAD']) != revision:
        raise ValueError('Checkout HEAD differs from requested revision')
    if capture(['git', 'status', '--porcelain', '--untracked-files=all']):
        raise ValueError('Build requires a clean checkout, including untracked source files')


def executable(name):
    found = shutil.which(name + '.cmd') if os.name == 'nt' else None
    found = found or shutil.which(name)
    if not found:
        raise ValueError('Missing build tool: ' + name)
    return found


def safe_extract(archive, destination):
    """Model archives must be plain files/directories; no traversal, links or devices."""
    destination = destination.resolve()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        total = 0
        names = set()
        for member in members:
            path = (destination / member.name).resolve()
            if Path(member.name).is_absolute() or (path != destination and destination not in path.parents):
                raise ValueError('Archive path escapes destination')
            if not (member.isdir() or member.isfile()) or path in names:
                raise ValueError('Archive contains duplicate/link/device entry')
            names.add(path)
            total += member.size
        if shutil.disk_usage(destination.parent).free < total + 2 * 1024**3:
            raise ValueError('Insufficient disk for model archive extraction')
        destination.mkdir()
        tar.extractall(destination, members=members)


def model_source(args, work):
    if args.models:
        return args.models.resolve()
    url = os.environ.get('INSIGHT_MODELS_URL', '')
    expected = os.environ.get('INSIGHT_MODELS_SHA256', '')
    if not url.startswith('https://') or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Linux needs --models or HTTPS INSIGHT_MODELS_URL and SHA256')
    archive = work / 'models.tar'
    # URL is configuration, not shell text; never print signed URL or credentials.
    with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as stream:
        shutil.copyfileobj(response, stream, length=4 * 1024 * 1024)
    if digest(archive) != expected:
        raise ValueError('Model archive SHA256 mismatch')
    destination = work / 'models'
    safe_extract(archive, destination)
    archive.unlink()
    return destination


def image_info(reference):
    return json.loads(capture(['docker', 'image', 'inspect', reference]))[0]


def require_pinned_image(reference):
    if not re.fullmatch(r'(?:sha256:|[^\s]+@sha256:)[0-9a-f]{64}', reference or ''):
        raise ValueError('Images must use local sha256 ID or registry reference@sha256:digest')
    if '@sha256:' in reference:
        run(['docker', 'pull', '--platform', 'linux/amd64', reference])
    info = image_info(reference)
    if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
        raise ValueError('Linux images must be linux/amd64')
    return info


def build_linux(args, output, work, version, revision):
    runtime = require_pinned_image(args.runtime_image)
    web = require_pinned_image(args.web_image)
    models = model_source(args, work)
    tag = 'insight:build-' + revision
    # Tag loaded by the offline package has a content-derived name.
    web_tag = 'insight-nginx:build-' + web['Id'].split(':')[1][:16]
    run(['docker', 'tag', web['Id'], web_tag])
    run(['docker', 'build', '--network', 'none', '-f', 'deploy/linux/Dockerfile',
         '--build-arg', 'RUNTIME_IMAGE=' + runtime['Id'], '--build-arg', 'REVISION=' + revision,
         '--build-arg', 'APP_VERSION=' + version, '-t', tag, '.'])
    actual_version = capture(['docker', 'run', '--rm', '--network', 'none', '--read-only', tag,
                              'python', '-c', 'from src import __version__; print(__version__)'])
    if actual_version != version:
        raise ValueError('Image application version differs from release version')
    bundle = work / 'insight-linux'
    run([sys.executable, 'deploy/linux/package.py', '--kind', args.linux_package,
         '--app-image', tag, '--web-image', web_tag, '--models', models,
         '--data-schema', args.data_schema, '--output', bundle])
    archive = output / f'Insight-{version}-linux-amd64-{revision[:12]}-{args.linux_package}.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
        tar.add(bundle, arcname='insight-linux')
    shutil.copyfile(bundle / 'release.json', output / 'linux-bundle.json')
    with (output / 'python-dependencies.txt').open('w', encoding='utf-8') as stream:
        run(['docker', 'run', '--rm', '--network', 'none', tag, 'python', '-m', 'pip', 'freeze', '--all'], stdout=stream)
    return dict(runtime_image=runtime['Id'], proxy_image=web['Id'], app_image=image_info(tag)['Id'],
                package_kind=args.linux_package, model_manifest_sha256=digest(bundle / 'release.json'))


def build_desktop(args, output, work, version, revision):
    run([sys.executable, '-m', 'PyInstaller', 'build/insight.spec', '--noconfirm',
         '--distpath', work / 'dist', '--workpath', work / 'pyinstaller'])
    if args.platform == 'windows':
        installer = output / f'Insight-{version}-windows-x64-{revision[:12]}.exe'
        run([executable('makensis'), '/NOCD', '/DAPPVERSION=' + version,
             '/DAPPDIST=' + str(work / 'dist/Insight'), '/DOUTFILE=' + str(installer), 'build/insight.nsi'])
    else:
        app = work / 'dist/Insight.app'
        with (app / 'Contents/Info.plist').open('rb') as stream:
            if plistlib.load(stream)['CFBundleShortVersionString'] != version:
                raise ValueError('macOS bundle version differs from source')
        run(['codesign', '--force', '--deep', '--sign', '-', app])
        run(['codesign', '--verify', '--deep', '--strict', app])
        staging = work / 'dmg'
        staging.mkdir()
        run(['ditto', app, staging / 'Insight.app'])
        (staging / 'Applications').symlink_to('/Applications')
        run(['hdiutil', 'create', '-volname', 'Insight', '-srcfolder', staging, '-ov', '-format', 'UDZO',
             output / f'Insight-{version}-macos-arm64-{revision[:12]}.dmg'])
    with (output / 'python-dependencies.txt').open('w', encoding='utf-8') as stream:
        run([sys.executable, '-m', 'pip', 'freeze', '--all'], stdout=stream)
    return {'signing': 'ad-hoc, not notarized' if args.platform == 'macos' else 'unsigned'}


def write_manifest(output, version, revision, target, details):
    manifest = dict(format=1, version=version, revision=revision, platform=target,
                    architecture={'windows': 'x64', 'macos': 'arm64', 'linux': 'amd64'}[target],
                    build_status='built', acceptance_status='pending', publish_status='not_published',
                    created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    tools=dict(python=platform.python_version(), os=platform.platform(),
                               node=capture([executable('node'), '--version']),
                               pnpm=capture([executable('pnpm'), '--version'])),
                    inputs={name: digest(ROOT / name) for name in
                            ('requirements.txt', 'frontend/pnpm-lock.yaml', 'src/__init__.py')},
                    details=details,
                    workflow={key: os.environ.get(key) for key in (
                        'GITHUB_WORKFLOW_REF', 'GITHUB_WORKFLOW_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')},
                    files={p.name: {'size': p.stat().st_size, 'sha256': digest(p)}
                           for p in sorted(output.iterdir()) if p.is_file()})
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    sums = ''.join(f'{digest(p)}  {p.name}\n' for p in sorted(output.iterdir()) if p.is_file())
    (output / 'SHA256SUMS').write_text(sums, encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=['windows', 'macos', 'linux'], required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='New disposable build directory outside checkout')
    parser.add_argument('--linux-package', choices=['full', 'upgrade'], default='upgrade')
    parser.add_argument('--runtime-image', default=os.environ.get('INSIGHT_RUNTIME_IMAGE'))
    parser.add_argument('--web-image', default=os.environ.get('INSIGHT_WEB_IMAGE'))
    parser.add_argument('--models', type=Path, default=os.environ.get('INSIGHT_MODELS_DIR'))
    parser.add_argument('--data-schema', default='insight-0.1.1')
    args = parser.parse_args()
    verify_checkout(args.revision)
    expected = {'windows': ('Windows', 'amd64'), 'macos': ('Darwin', 'arm64'), 'linux': ('Linux', 'x86_64')}
    system, machine = expected[args.platform]
    actual_machine = platform.machine().lower()
    if platform.system() != system or actual_machine != machine:
        raise ValueError(f'{args.platform} requires {system}/{machine}, got {platform.system()}/{actual_machine}')
    output, work = args.output.resolve(), args.work.resolve()
    for path in (output, work):
        if path.exists() or path == ROOT or ROOT in path.parents:
            raise ValueError('Work/output must be new directories outside source checkout')
    if output in work.parents or work in output.parents or output == work:
        raise ValueError('Work and output must be separate directories')
    if args.platform == 'linux' and args.models:
        models = args.models.resolve()
        if models == work or models == output or work in models.parents or output in models.parents:
            raise ValueError('Model source must be separate from build output/work')
    run([sys.executable, 'scripts/check_release.py'])
    version = read_version()
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    try:
        run([executable('pnpm'), '--dir', 'frontend', 'install', '--frozen-lockfile'])
        run([executable('pnpm'), '--dir', 'frontend', 'run', 'build'])
        if args.platform == 'linux':
            details = build_linux(args, output, work, version, args.revision)
        else:
            details = build_desktop(args, output, work, version, args.revision)
        verify_checkout(args.revision)
        manifest = write_manifest(output, version, args.revision, args.platform, details)
        print(json.dumps({'version': version, 'revision': args.revision, 'platform': args.platform,
                          'build_status': manifest['build_status'], 'acceptance_status': 'pending'}))
    except Exception:
        # Never emit a success manifest for an incomplete build.
        (output / 'BUILD_FAILED.txt').write_text('Incomplete build. Do not publish these files.\n')
        raise


if __name__ == '__main__':
    main()
