#!/usr/bin/env python3
"""Assemble a checked offline bundle from already-built images (does not publish)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inspect(image):
    return json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['full', 'upgrade'], required=True)
    parser.add_argument('--app-image', required=True)
    parser.add_argument('--web-image', required=True)
    parser.add_argument('--models', type=Path, required=True, help='Approved model directory, also used to specify upgrade prerequisites')
    parser.add_argument('--output', type=Path, required=True, help='New bundle directory, never overwrite')
    parser.add_argument('--data-schema', required=True, help='Reviewed compatibility identifier, e.g. insight-0.1.1')
    parser.add_argument('--runtime', type=Path, help='Optional operator-reviewed Docker/Compose installation materials')
    args = parser.parse_args()
    app, web = inspect(args.app_image), inspect(args.web_image)
    labels = app['Config'].get('Labels') or {}
    revision = labels.get('org.opencontainers.image.revision', '')
    version = labels.get('org.opencontainers.image.version', '')
    if not re.fullmatch('[0-9a-f]{40}', revision) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][\w.-]+)?', version):
        raise ValueError('Image must declare version and full source commit labels')
    for image in (app, web):
        if image['Architecture'] != 'amd64' or image['Os'] != 'linux':
            raise ValueError('Only linux/amd64 images are supported by this bundle')
    source = args.models.resolve()
    required = [source / 'huggingface/hub/models--BAAI--bge-large-zh-v1.5/refs/main',
                source / 'reranker-torch/config.json', source / 'tiktoken']
    if not all(p.exists() for p in required):
        raise ValueError('Model layout does not match config.local.example.yaml')
    hf = source / 'huggingface/hub/models--BAAI--bge-large-zh-v1.5'
    model_revision = (hf / 'refs/main').read_text().strip()
    if not re.fullmatch('[0-9a-f]{40}', model_revision):
        raise ValueError('Embedding model ref must identify a fixed snapshot')
    snapshot = hf / 'snapshots' / model_revision
    for directory in (snapshot, source / 'reranker-torch'):
        for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json'):
            if not (directory / name).is_file():
                raise ValueError('Incomplete model: ' + str(directory / name))
        if not any((directory / name).is_file() for name in ('pytorch_model.bin', 'model.safetensors')):
            raise ValueError('Full model weights are missing: ' + str(directory))
    if not (snapshot / 'modules.json').is_file() or not (snapshot / '1_Pooling/config.json').is_file():
        raise ValueError('Embedding pooling configuration is missing')
    if not any(p.is_file() for p in (source / 'tiktoken').iterdir()):
        raise ValueError('Tokenizer cache is empty')
    if args.output.exists():
        raise ValueError('Output already exists')
    out = args.output.resolve()
    out.mkdir(parents=True)
    templates = Path(__file__).resolve().parent
    for name in ('compose.yaml', 'nginx.conf', 'insightctl.py', 'settings.example.json', 'config.local.example.yaml', 'README.md'):
        shutil.copyfile(templates / name, out / name)
    models = {}
    for path in sorted(source.rglob('*')):
        if path.is_file():
            name = path.relative_to(source).as_posix()
            if source not in path.resolve().parents:
                raise ValueError('Model link escapes model directory: ' + name)
            models[name] = digest(path)
    if not models:
        raise ValueError('Empty model directory')
    if args.kind == 'full':
        shutil.copytree(source, out / 'models', symlinks=False)
    if args.runtime:
        shutil.copytree(args.runtime, out / 'runtime', symlinks=False)
    subprocess.run(['docker', 'save', '-o', str(out / 'images.tar'), args.app_image, args.web_image], check=True)
    release = dict(format=1, kind=args.kind, version=version, revision=revision, architecture='amd64',
                   data_schema=args.data_schema, app_image=args.app_image, web_image=args.web_image,
                   image_ids={args.app_image: app['Id'], args.web_image: web['Id']}, model_checksums=models,
                   runtime_included=bool(args.runtime))
    (out / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    sums = {p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out / 'checksums.json').write_text(json.dumps(sums, indent=2) + '\n')
    print('Bundle prepared:', out)
    print('Directory checksums verify consistency, not authenticity. Deliver through a trusted channel.')


if __name__ == '__main__':
    main()
