"""Prepare public, pinned build materials on a disposable Linux AMD64 runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def verified_download(url, destination, expected, algorithm='sha256', size=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial')
    for attempt in range(3):
        try:
            digest = hashlib.sha256() if algorithm == 'sha256' else hashlib.sha1()
            if algorithm == 'git-blob-sha1':
                digest.update(f'blob {size}\0'.encode())
            total = 0
            with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as output:
                while block := response.read(4 * 1024 * 1024):
                    output.write(block)
                    digest.update(block)
                    total += len(block)
            if digest.hexdigest() != expected or (size is not None and size != total):
                raise ValueError('Downloaded material failed integrity check: ' + destination.name)
            partial.replace(destination)
            return
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def prepare_models(manifest, destination):
    for model in manifest['models']:
        if model['target'] == 'embedding':
            hub = destination / 'huggingface/hub' / ('models--' + model['repo'].replace('/', '--'))
            folder = hub / 'snapshots' / model['revision']
            (hub / 'refs').mkdir(parents=True)
            (hub / 'refs/main').write_text(model['revision'])
        else:
            folder = destination / 'reranker-torch'
        for entry in model['files']:
            name = entry['name']
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('Invalid model manifest path')
            print('Download', model['repo'], name, flush=True)
            verified_download(f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{name}",
                              folder / name, entry['hash'], entry['algorithm'], entry['size'])
    for entry in manifest['tokenizers']:
        verified_download(entry['url'], destination / 'tiktoken' / entry['cache_key'], entry['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('Run only on disposable Linux AMD64 build hosts')
    work = args.work.resolve()
    if work.exists() or work == ROOT or ROOT in work.parents:
        raise ValueError('Use a new directory outside checkout')
    work.mkdir(parents=True)
    if shutil.disk_usage(work).free < 12 * 1024**3:
        raise ValueError('Need at least 12 GiB free for build materials and package')
    manifest = json.loads((ROOT / 'deploy/linux/ci-materials.json').read_text())
    context = work / 'runtime'
    context.mkdir()
    for name in ('Dockerfile.runtime', 'requirements-runtime.lock'):
        shutil.copyfile(ROOT / 'deploy/linux' / name, context / name)
    subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '-f', str(context / 'Dockerfile.runtime'),
                    '--build-arg', 'PYTHON_IMAGE=' + manifest['images']['python'],
                    '--iidfile', str(work / 'runtime.id'), str(context)], check=True)
    models = work / 'models'
    prepare_models(manifest, models)
    runtime = (work / 'runtime.id').read_text().strip()
    # Inputs are checked-in public references, never account/server configuration.
    with open(os.environ['GITHUB_ENV'], 'a') as env:
        env.write(f"INSIGHT_RUNTIME_IMAGE={runtime}\nINSIGHT_WEB_IMAGE={manifest['images']['nginx']}\n")
        env.write(f'INSIGHT_MODELS_DIR={models}\n')
    print('Public Linux build materials ready', flush=True)


if __name__ == '__main__':
    main()
