"""src/core/security.py 关键路径测试。

覆盖 S-2 SSRF 防护、S-3 ZIP Slip 防护、S-4 文件名穿越防护。
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core.security import (
    SecurityError,
    ensure_within_path,
    sanitize_filename,
    validate_external_url,
)


# ===== S-2: SSRF =====


def test_validate_external_url_allows_https_public():
    """公网 https 应通过。"""
    # 注意：此测试需要网络（DNS 解析）
    try:
        result = validate_external_url("https://api.anthropic.com")
        assert result == "https://api.anthropic.com"
    except SecurityError:
        pytest.skip("网络不可用，跳过 DNS 解析")


def test_validate_external_url_blocks_loopback():
    """127.0.0.1 应拒绝（即使 https）。"""
    with pytest.raises(SecurityError):
        validate_external_url("https://127.0.0.1/")


def test_validate_external_url_blocks_metadata_endpoint():
    """云元数据地址 169.254.169.254 应拒绝。"""
    with pytest.raises(SecurityError):
        validate_external_url("http://169.254.169.254/latest/meta-data/", allow_http=True)


def test_validate_external_url_blocks_private_10x():
    """10.x 私网段应拒绝。"""
    with pytest.raises(SecurityError):
        validate_external_url("http://10.0.0.1/", allow_http=True)


def test_validate_external_url_blocks_file_protocol():
    """file:// 应拒绝（防 file:///etc/passwd）。"""
    with pytest.raises(SecurityError):
        validate_external_url("file:///etc/passwd")


def test_validate_external_url_rejects_http_by_default():
    """默认禁止 http://（要求 https）。"""
    with pytest.raises(SecurityError):
        validate_external_url("http://example.com")


# ===== S-3: ZIP Slip =====


def test_ensure_within_path_allows_inside():
    """合法子路径应通过。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        target = ensure_within_path(Path("subdir/file.md"), base)
        # resolve 后仍应在 base 内
        assert str(target).startswith(str(base))


def test_ensure_within_path_blocks_dotdot():
    """../../../etc/passwd 应拒绝。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        with pytest.raises(SecurityError):
            ensure_within_path(Path("../../../etc/passwd"), base)


def test_ensure_within_path_blocks_absolute():
    """绝对路径应拒绝（如果不在 base 内）。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        with pytest.raises(SecurityError):
            ensure_within_path(Path("/etc/passwd"), base)


# ===== S-4: 文件名穿越 =====


def test_sanitize_filename_normal():
    """普通文件名应通过。"""
    assert sanitize_filename("normal.md") == "normal.md"


def test_sanitize_filename_takes_basename():
    """含路径分隔符取 basename。"""
    assert sanitize_filename("subdir/file.md") == "file.md"
    assert sanitize_filename("a/b/c/file.md") == "file.md"


def test_sanitize_filename_blocks_dotdot_to_basename():
    """.. 应被吃成 basename（取 basename 后再过黑名单）。"""
    # 非 sensitive 的 basename 应通过
    assert sanitize_filename("../../config.toml") == "config.toml"
    # sensitive 的 basename 应被黑名单拦下
    with pytest.raises(SecurityError):
        sanitize_filename("../../.bashrc")


def test_sanitize_filename_blocks_absolute():
    """绝对路径取 basename。"""
    assert sanitize_filename("/etc/passwd") == "passwd"


def test_sanitize_filename_blocks_sensitive_hidden():
    """敏感 . 开头文件应拒绝（.bashrc / .env / .ssh 等）。"""
    for name in [".bashrc", ".zshrc", ".env", ".ssh", ".gitconfig"]:
        with pytest.raises(SecurityError):
            sanitize_filename(name)


def test_sanitize_filename_allows_legit_hidden():
    """合法的 .gitignore / .env.example 应允许。"""
    assert sanitize_filename(".gitignore") == ".gitignore"
    assert sanitize_filename(".env.example") == ".env.example"
    assert sanitize_filename(".dockerignore") == ".dockerignore"


def test_sanitize_filename_blocks_control_chars():
    """控制字符应拒绝。"""
    with pytest.raises(SecurityError):
        sanitize_filename("file\x00.txt")


def test_sanitize_filename_blocks_windows_reserved():
    """Windows 保留名应拒绝。"""
    for name in ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1"]:
        with pytest.raises(SecurityError):
            sanitize_filename(f"{name}.txt")
