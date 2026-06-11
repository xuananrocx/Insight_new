"""程序包解包提取模块。

支持：tar.gz / tar.bz2 / tar.xz / zip / tar
策略：
1. 把包解压到 data/extracted/<pkg_stem>__<hash8>/ 下
2. 扫描解压目录里的所有"白名单"文件（配置中 ingest.text_extensions）
3. 跳过二进制文件、超大文件、系统隐藏文件
4. 返回解压后的目录路径，由 ingestion 模块继续处理
"""
from __future__ import annotations

import hashlib
import shutil
import tarfile
import zipfile
from pathlib import Path

from src.core.config import settings


class ExtractError(Exception):
    """解包失败。"""


# 单文件大小上限：50 MB（避免把超大 dump 文件塞进知识库）
_MAX_FILE_SIZE = 50 * 1024 * 1024
# 压缩包解压后总大小上限：500 MB
_MAX_EXTRACTED_SIZE = 500 * 1024 * 1024
# 跳过的目录名（开发约定俗成的"垃圾"目录）
_SKIP_DIRS = {
    "__pycache__", ".git", ".svn", ".hg", "node_modules",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env",
}
# 跳过的文件名前缀
_SKIP_FILE_PREFIXES = (".", "__")
# 压缩包内部禁止绝对路径/.. 路径（防止 zip slip）


def _is_safe_path(member_name: str) -> bool:
    """判断解压成员路径是否安全（防 zip slip）。"""
    p = Path(member_name)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    return True


def _file_hash(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_dir_name(pkg_path: Path) -> str:
    """生成解压目标目录名：包名__前8位hash。"""
    h = _file_hash(pkg_path)[:8]
    return f"{pkg_path.stem}__{h}"


def extract_package(pkg_path: Path, force: bool = False) -> Path:
    """解压压缩包到 data/extracted/<pkg_stem>__<hash8>/。

    返回解压后的目录路径。如果已解压过且 force=False，直接返回。
    """
    if not pkg_path.exists():
        raise ExtractError(f"压缩包不存在: {pkg_path}")

    extracted_root = settings.get_path("extracted")
    target_dir = extracted_root / _extract_dir_name(pkg_path)
    if target_dir.exists() and not force:
        # 已解压过，直接返回
        return target_dir
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    name = pkg_path.name.lower()
    try:
        if name.endswith(".zip"):
            _extract_zip(pkg_path, target_dir)
        elif name.endswith((".tar.gz", ".tgz")):
            _extract_tar(pkg_path, target_dir, "r:gz")
        elif name.endswith((".tar.bz2", ".tbz2")):
            _extract_tar(pkg_path, target_dir, "r:bz2")
        elif name.endswith((".tar.xz", ".txz")):
            _extract_tar(pkg_path, target_dir, "r:xz")
        elif name.endswith(".tar"):
            _extract_tar(pkg_path, target_dir, "r:")
        else:
            raise ExtractError(f"不支持的压缩包格式: {pkg_path.name}")
    except ExtractError:
        raise
    except Exception as e:
        # 解压失败 → 清理半成品
        shutil.rmtree(target_dir, ignore_errors=True)
        raise ExtractError(f"解压 {pkg_path.name} 失败: {e}") from e

    return target_dir


def _extract_zip(pkg_path: Path, target: Path) -> None:
    total = 0
    with zipfile.ZipFile(pkg_path) as zf:
        for info in zf.infolist():
            if not _is_safe_path(info.filename):
                continue
            if info.is_dir():
                continue
            if info.file_size > _MAX_FILE_SIZE:
                continue
            total += info.file_size
            if total > _MAX_EXTRACTED_SIZE:
                raise ExtractError(f"压缩包 {pkg_path.name} 解压后超过 {_MAX_EXTRACTED_SIZE // 1024 // 1024}MB 限制")
            zf.extract(info, target)


def _extract_tar(pkg_path: Path, target: Path, mode: str) -> None:
    total = 0
    with tarfile.open(pkg_path, mode) as tf:
        for member in tf.getmembers():
            if not _is_safe_path(member.name):
                continue
            if not member.isfile():
                continue
            if member.size > _MAX_FILE_SIZE:
                continue
            total += member.size
            if total > _MAX_EXTRACTED_SIZE:
                raise ExtractError(f"压缩包 {pkg_path.name} 解压后超过 {_MAX_EXTRACTED_SIZE // 1024 // 1024}MB 限制")
            tf.extract(member, target)


def list_extractable_files(extracted_dir: Path) -> list[Path]:
    """扫描解压目录，返回符合白名单条件的文件列表。

    跳过：
    - 在 _SKIP_DIRS 中的目录
    - 隐藏文件/前缀为 _SKIP_FILE_PREFIXES
    - 大小超过 _MAX_FILE_SIZE 的文件
    - 不在白名单扩展名的文件
    """
    allowed_exts = settings.config.get("ingest", {}).get("text_extensions", [])
    allowed_set = {e.lower().lstrip(".") for e in allowed_exts}

    result: list[Path] = []
    for path in extracted_dir.rglob("*"):
        if not path.is_file():
            continue
        # 检查路径中是否有跳过目录
        try:
            rel_parts = path.relative_to(extracted_dir).parts
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        # 跳过隐藏/双下划线开头文件
        fname = path.name
        if fname.startswith(_SKIP_FILE_PREFIXES):
            continue
        # 大小检查
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_SIZE:
            continue
        # 扩展名白名单
        ext = path.suffix.lower().lstrip(".")
        if ext not in allowed_set:
            continue
        result.append(path)
    return result


def is_package(path: Path) -> bool:
    """判断是否为支持的压缩包。"""
    name = path.name.lower()
    return name.endswith((".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2",
                          ".tar.xz", ".txz", ".tar"))
