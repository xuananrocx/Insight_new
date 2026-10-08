"""Portable pack validation. Runs before creating any destination KB."""
import hashlib
import json
import math
from pathlib import PurePosixPath


def portable_path(value):
    value = str(value).replace('\\', '/')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or ':' in value or '\x00' in value or str(path) == '.':
        raise ValueError(f'知识库包路径不安全: {value!r}')
    return str(path)


def inspect_pack(zf, prefix, manifest):
    names = {}
    for item in zf.infolist():
        if item.is_dir():
            continue
        name = portable_path(item.filename)
        if name in names:
            raise ValueError(f'知识库包有重复文件路径: {name}')
        names[name] = item.filename
    files = {}
    owners = {}
    for file in manifest['files']:
        rel = portable_path(file['relative_path'])
        if rel in files:
            raise ValueError(f'文档路径重复: {rel}')
        arc = prefix + 'documents/' + rel
        if arc not in names:
            raise ValueError(f'知识库包缺少原文档: {rel}，请在原库补齐文件后重新导出')
        digest = hashlib.sha256()
        size = 0
        with zf.open(names[arc]) as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                digest.update(block)
        if file.get('content_hash') and digest.hexdigest() != file['content_hash']:
            raise ValueError(f'原文档校验失败: {rel}')
        if 'file_size' in file and size != file['file_size']:
            raise ValueError(f'原文档大小不符: {rel}')
        files[rel] = file
        file['relative_path'] = rel
        for cid in file.get('chunk_ids', []):
            if cid in owners:
                raise ValueError(f'分块被多个文档重复引用: {cid}')
            owners[cid] = rel
    seen = set()
    old_files = {}
    orphan_summaries = []
    dim = manifest['kb'].get('embedding_dim')
    with zf.open(prefix + 'vectors/chunks.jsonl') as stream:
        for line in stream:
            if not line.strip():
                continue
            chunk = json.loads(line)
            cid = str(chunk['id'])
            if not cid or cid in seen:
                raise ValueError(f'源分块 ID 重复或为空: {cid}')
            seen.add(cid)
            vector = chunk['vector']
            if not vector or (dim and len(vector) != dim) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                raise ValueError(f'分块向量无效: {cid}')
            if not isinstance(chunk['text'], str):
                raise ValueError(f'分块正文无效: {cid}')
            meta = chunk.get('metadata') or {}
            rel = owners.get(cid)
            fid = meta.get('file_id')
            if rel and fid is not None:
                if old_files.get(fid, rel) != rel:
                    raise ValueError(f'源文档 ID 关联冲突: {fid}')
                old_files[fid] = rel
            if rel is None:
                if meta.get('chunk_type') != 'summary':
                    raise ValueError(f'分块没有文档关联: {cid}')
                orphan_summaries.append((cid, meta))
    if set(owners) - seen:
        raise ValueError('知识库包缺少清单引用的向量分块')
    for cid, meta in orphan_summaries:
        rel = old_files.get(meta.get('file_id'))
        if rel is None:
            candidates = [r for r, f in files.items() if f.get('content_hash') == meta.get('content_hash')
                          and PurePosixPath(r).name == meta.get('source_name')]
            if len(candidates) == 1:
                rel = candidates[0]
        if rel is None:
            raise ValueError(f'摘要没有唯一文档关联: {cid}')
        owners[cid] = rel
    return names, owners, len(seen)
