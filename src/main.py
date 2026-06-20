"""AMD AI Assistant - 主启动入口。

同时启动 FastAPI 后端和 Streamlit 前端。

使用方法：
    python -m src.main                # 启动两个服务
    python -m src.main --api-only      # 只启 API
    python -m src.main --web-only      # 只启 Streamlit
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def start_api(host: str, port: int) -> subprocess.Popen:
    """启动 FastAPI（uvicorn）。

    --reload 仅在 dev 环境启用（生产环境性能差、长连接会被 reload 中断）。
    """
    is_dev = os.environ.get("APP_ENV", "dev") == "dev"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if is_dev:
        cmd.append("--reload")
    print(f"[启动 FastAPI] {' '.join(cmd)} (env={os.environ.get('APP_ENV', 'dev')})")
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT)


def start_web(host: str, port: int) -> subprocess.Popen:
    """启动 Streamlit。"""
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "src/web/streamlit_app.py",
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    print(f"[启动 Streamlit] {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=PROJECT_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="AMD AI Assistant 启动器")
    parser.add_argument("--api-only", action="store_true", help="只启 FastAPI")
    parser.add_argument("--web-only", action="store_true", help="只启 Streamlit")
    args = parser.parse_args()

    # 确保 data 目录存在
    from src.core.config import settings

    settings.ensure_data_dirs()

    # 加载端口配置
    server_cfg = settings.config["server"]
    api_host = server_cfg["api_host"]
    api_port = server_cfg["api_port"]
    web_host = server_cfg["web_host"]
    web_port = server_cfg["web_port"]

    processes: list[subprocess.Popen] = []

    try:
        if args.web_only:
            processes.append(start_web(web_host, web_port))
        elif args.api_only:
            processes.append(start_api(api_host, api_port))
        else:
            # 先起 API，再起 Web
            processes.append(start_api(api_host, api_port))
            print("[等待] FastAPI 启动中...")
            time.sleep(2)
            processes.append(start_web(web_host, web_port))

        print("\n" + "=" * 50)
        print(f"🤖 {settings.app_name} 已启动")
        print(f"   API:   http://{api_host}:{api_port}")
        print(f"   API 文档: http://{api_host}:{api_port}/docs")
        print(f"   Web UI: http://{web_host}:{web_port}")
        print("=" * 50)
        print("\n按 Ctrl+C 停止所有服务\n")

        # 等待子进程（或被 Ctrl+C）
        for p in processes:
            p.wait()

    except KeyboardInterrupt:
        print("\n[停止] 收到中断信号，正在关闭...")
    finally:
        for p in processes:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        print("[完成] 所有服务已停止。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
