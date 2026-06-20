"""PyInstaller 打包用的启动入口。

直接在进程内调 uvicorn.run()，避免 subprocess（PyInstaller 打包后 sys.executable
指向 .app/.exe 自己，subprocess 启动子进程会失败）。

启动后：
- FastAPI 后端在 8000 端口
- 前端 dist 由 FastAPI 静态服务同源提供（http://localhost:8000）
"""
from __future__ import annotations

import webbrowser
from threading import Timer

import uvicorn

from src.api.main import app
from src.core.config import settings


def _open_browser_later(url: str, delay: float = 2.0) -> None:
    """延迟打开浏览器，等服务起来后再开。"""
    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    Timer(delay, _open).start()


def main() -> None:
    server_cfg = settings.config["server"]
    host = server_cfg.get("api_host", "127.0.0.1")
    port = int(server_cfg.get("api_port", 8000))
    version = settings.config.get("app", {}).get("version", "0.0.0")

    print("=" * 50)
    print(f"  {settings.app_name} v{version}")
    print(f"  API:   http://{host}:{port}")
    print(f"  Docs:  http://{host}:{port}/docs")
    print("=" * 50)

    # 启动后自动开浏览器
    _open_browser_later(f"http://{host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
