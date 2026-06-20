"""安全工具：URL 白名单 / 路径穿越防护 / pickle 替代方案。"""
from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse


class SecurityError(ValueError):
    """输入不通过安全校验。"""


# ===== SSRF 防护 =====

# 禁止的 host：私有网段 / 回环 / 链路本地 / 云元数据
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # IPv4 loopback
    ipaddress.ip_network("10.0.0.0/8"),         # private
    ipaddress.ip_network("172.16.0.0/12"),      # private
    ipaddress.ip_network("192.168.0.0/16"),     # private
    ipaddress.ip_network("169.254.0.0/16"),     # link-local（含云元数据）
    ipaddress.ip_network("0.0.0.0/8"),          # reserved
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def validate_external_url(url: str, *, allow_http: bool = False, allow_private_ip: bool = False) -> str:
    """校验 base_url 是否指向公网（防止 SSRF 探内网/云元数据）。

    Args:
        url: 用户配置的 base_url，如 https://api.anthropic.com
        allow_http: 允许 http://（默认 False，强制 https）
        allow_private_ip: 允许解析到内网 IP（默认 False，用于 anthropic-arch 等可信私有服务）

    Returns: 校验通过的 url（trailing slash 处理由调用方做）

    Raises:
        SecurityError: URL 不合法 / 协议不允许 / host 在禁止名单
    """
    if not url or not isinstance(url, str):
        raise SecurityError("URL 为空")

    parsed = urlparse(url.strip())
    if not parsed.scheme:
        raise SecurityError("URL 缺少 scheme（应为 https://）")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SecurityError(f"不支持的协议: {scheme}（仅允许 http/https）")
    if scheme == "http" and not allow_http:
        raise SecurityError("禁止 http://（仅允许 https://）")

    host = parsed.hostname or ""
    if not host:
        raise SecurityError("URL 缺少 host")

    # 名单拦截（云元数据等已知危险 host）
    if host.lower() in _BLOCKED_HOSTS:
        raise SecurityError(f"禁止访问: {host}")

    # 解析所有 IP（含域名解析的所有 A/AAAA 记录）
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise SecurityError(f"域名解析失败: {host} ({e})")

    ips = {info[4][0] for info in infos}
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        # 如果允许私有 IP（如 anthropic-arch），跳过内网检查
        if allow_private_ip:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise SecurityError(
                    f"禁止访问内网/回环地址: {host} -> {ip}（匹配 {net}）"
                )

    return url.strip()


# ===== 路径穿越防护 =====


def sanitize_filename(name: str, *, max_len: int = 255) -> str:
    """清洗上传文件名：取 basename + 拒绝特殊字符。

    防止: ../../.bashrc、绝对路径、空字节、Windows 保留名。
    """
    if not name or not isinstance(name, str):
        raise SecurityError("文件名为空")

    # 取 basename（去掉任何路径分隔符前面的部分）
    # 同时处理 \ 和 /（Windows 路径）
    cleaned = name.replace("\\", "/").split("/")[-1]

    if not cleaned or cleaned in (".", ".."):
        raise SecurityError(f"非法文件名: {name!r}")

    # 拒绝空字节 / 控制字符
    if any(ord(c) < 32 for c in cleaned):
        raise SecurityError(f"文件名含控制字符: {name!r}")

    # 拒绝 Windows 保留名
    stem = cleaned.split(".")[0].upper()
    WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
        f"COM{i}" for i in range(1, 10)
    } | {f"LPT{i}" for i in range(1, 10)}
    if stem in WIN_RESERVED:
        raise SecurityError(f"Windows 保留名: {cleaned}")

    # 拒绝敏感隐藏文件名（shell/ssh/git 等可能被攻击者覆盖的配置）
    # 允许 .gitignore / .env.example 等用户合法文档
    SENSITIVE_HIDDEN = {
        ".bashrc", ".bash_profile", ".bash_logout", ".bash_history",
        ".zshrc", ".zsh_history", ".zprofile",
        ".profile",
        ".ssh",  # 目录或文件名
        ".env",  # 真 .env（不含 .example/.sample 等扩展）
        ".gitconfig", ".npmrc", ".yarnrc", ".pypirc",
        ".aws", ".docker", ".kube",
        ".netrc", ".pgpass", ".my.cnf",
        ".wgetrc", ".curlrc",
    }
    if cleaned in SENSITIVE_HIDDEN:
        raise SecurityError(f"拒绝敏感文件名: {cleaned}")

    if len(cleaned) > max_len:
        raise SecurityError(f"文件名过长 (>{max_len} 字符)")

    return cleaned


def ensure_within_path(path: Path, base: Path) -> Path:
    """校验 path resolve 后仍在 base 内（防 zip slip / 软链接逃逸）。

    Args:
        path: 待校验的路径（可能含 .. 或绝对路径）
        base: 允许的根目录

    Returns: resolve 后的绝对路径

    Raises:
        SecurityError: 路径逃出 base 范围
    """
    base_resolved = base.resolve()
    try:
        target = (base / path).resolve() if not path.is_absolute() else path.resolve()
    except (OSError, ValueError) as e:
        raise SecurityError(f"路径解析失败: {path} ({e})")

    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise SecurityError(
            f"路径逃逸: {path} -> {target} 不在 {base_resolved} 内"
        )

    return target
