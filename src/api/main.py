"""FastAPI 主程序。

启动方式：
    uvicorn src.api.main:app --reload --port 8000

架构约束：
    所有数据访问（Chroma、SQLite）只在 FastAPI 进程内进行。
    Streamlit/其他客户端通过 HTTP 调用 API。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from src.api.access import AuthenticationMiddleware, authorize
from src.api.routes_accounts import router as accounts_router
from src.core import accounts
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import __version__
from src.api.routes_feedback import router as feedback_router
from src.api.routes_kbs import router as kbs_router
from src.api.routes_indexes import router as indexes_router
from src.api.routes_knowledge import router as knowledge_router
from src.api.routes_logs import router as logs_router
from src.api.routes_ai_logs import router as ai_logs_router
from src.api.routes_qa import router as qa_router
from src.api.routes_sessions import router as sessions_router
from src.api.routes_settings import router as settings_router
from src.core import llm_client, vector_store
from src.core.config import settings
from src.core.errors import EmbeddingDimensionMismatchError
from src.core.logging_config import cleanup_old_logs, get_log_level, setup_logging
from src.db import metadata_db


# 初始化日志（在任何业务 import 触发的日志之前）
setup_logging(default_level="INFO")
cleanup_old_logs()

logger = logging.getLogger(__name__)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    dependencies=[Depends(authorize)],
    docs_url=None,
    openapi_url=None,
    redoc_url=None,
)


# 全局异常处理器：业务异常转中文 HTTP 响应
app.add_middleware(AuthenticationMiddleware)
from src.api.routes_permissions import router as permissions_router
app.include_router(permissions_router)
app.include_router(accounts_router)

@app.exception_handler(EmbeddingDimensionMismatchError)
async def _handle_dim_mismatch(request: Request, exc: EmbeddingDimensionMismatchError):
    logger.warning(
        f"dim mismatch: collection={exc.collection_name} "
        f"expected={exc.expected_dim} got={exc.got_dim} path={request.url.path}"
    )
    return JSONResponse(
        status_code=400,
        content={
            "detail": (
                f"维度不匹配：当前 embedding 模型返回 {exc.got_dim} 维，"
                f"但 KB collection 已锁定为 {exc.expected_dim} 维。"
                f"请在该 KB 详情页点「重建」按当前模型重新投喂。"
            ),
            "error_type": "embedding_dimension_mismatch",
            "collection_name": exc.collection_name,
            "expected_dim": exc.expected_dim,
            "got_dim": exc.got_dim,
        },
    )


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    """记录 API 请求的 method/path/status/耗时。跳过静态资源。"""
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    start = time.time()
    try:
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        if response.status_code >= 400:
            logger.warning(f"{request.method} {path} → {response.status_code} {duration_ms:.0f}ms")
        else:
            logger.info(f"{request.method} {path} → {response.status_code} {duration_ms:.0f}ms")
        return response
    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        logger.exception(f"{request.method} {path} EXCEPTION {duration_ms:.0f}ms {e}")
        raise

# CORS 白名单：只允许本机前端访问（防止任意网页跨域调本地 API 窃取 key / 数据）
# 包含 dev server (5173) + prod (frontend/dist 经 FastAPI 静态服务，同源) + Streamlit (8501)
_ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8501",
    "http://localhost:8501",
    # 同源（前端打包后由 FastAPI 提供静态服务，端口同 API）
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Insight-Request"],
)


@app.on_event("startup")
def _on_startup() -> None:
    """启动时初始化。"""
    # 恢复持久化的日志级别（如果有）
    try:
        from src.db import metadata_db
        saved_level = metadata_db.get_state("log_level")
        if saved_level and isinstance(saved_level, str) and saved_level != get_log_level():
            from src.core.logging_config import set_log_level
            set_log_level(saved_level)
            logger.info(f"restored log level from state: {saved_level}")
    except Exception as e:
        logger.warning(f"failed to restore log level: {e}")

    # 打印初始化日志（路径迁移、config 版本等）
    for line in settings.init_logs:
        logger.info(f"[init] {line}")
    # 确保数据目录存在
    settings.ensure_data_dirs()
    # 初始化数据库 + schema 迁移
    metadata_db.init_db()
    accounts.init_auth()
    from src.knowledge import index_tasks
    index_tasks.recover()

    # BM25 依赖预检：缺失时 ingest 的 add_chunks 会静默失败（只记 warning），
    # 历史上因此建过空索引，这里显式报错提醒安装
    try:
        import jieba  # noqa: F401
        import rank_bm25  # noqa: F401
        from src.qa import bm25_index
        st = bm25_index.stats()
        logger.info(f"[init] BM25 deps ok, index chunks={st['chunk_count']}")
    except ImportError as e:
        logger.error(
            f"[init] BM25 依赖缺失（{e}），关键词检索将不生效。"
            f"请安装: pip install jieba rank-bm25"
        )

    # 检测批量上传任务中断（running 但实际进程已死）→ 标记 paused
    try:
        from src.knowledge import batch_upload
        recovered = batch_upload.recover_interrupted_tasks()
        if recovered > 0:
            logger.info(f"[init] recovered {recovered} interrupted batch upload tasks")
    except Exception as e:
        logger.warning(f"[init] recover interrupted batch upload tasks failed: {e}")

    # 清理过期 AI 调用日志
    try:
        retention = settings.config.get("feature_flags", {}).get("ai_call_log", {}).get("retention_days", 30)
        deleted = metadata_db.cleanup_expired_ai_call_logs(retention_days=retention)
        if deleted > 0:
            logger.info(f"[init] cleaned {deleted} expired ai_call_logs (>{retention}d)")
    except Exception as e:
        logger.warning(f"[init] cleanup ai_call_logs failed: {e}")

    # 收紧敏感文件权限（config.yaml / metadata.db / bm25_index.json 等含 api_key / 数据）
    try:
        import os
        from src.db import metadata_db as _mdb
        # metadata.db 路径反推 data_dir
        data_dir = _mdb._get_db_path().parent
        # config.yaml 在 data_dir 的上级（应用根目录）
        app_root = data_dir.parent
        for sensitive_name in ["config.yaml", ".env"]:
            p = app_root / sensitive_name
            if p.exists():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        # data_dir 下所有文件（metadata.db / bm25_index.json / logs/）
        if data_dir.exists():
            for f in data_dir.iterdir():
                if f.is_file():
                    try:
                        os.chmod(f, 0o600)
                    except OSError:
                        pass
                elif f.is_dir() and f.name == "logs":
                    for lf in f.iterdir():
                        if lf.is_file():
                            try:
                                os.chmod(lf, 0o600)
                            except OSError:
                                pass
    except Exception as e:
        logger.warning(f"收紧敏感文件权限失败（非致命）: {e}")

    # 检查上次 rebuild 是否被中断（向量库可能已清空）
    try:
        from src.knowledge import rebuild
        rebuild.recover_state_on_startup()
    except Exception as e:
        logger.warning(f"rebuild 状态恢复检查失败: {e}")

    # 后台预热本地 embedding 模型（bge-large 加载可达数十秒，
    # 不预热的话首次问答/首次进设置页会被同步加载卡住）
    try:
        client = llm_client.get_client()
        if client._embed_mode == "local":
            import threading

            def _prewarm_local_embedder() -> None:
                try:
                    client._get_local_embedder()
                    logger.info("embedding 模型预热完成")
                except Exception as e:
                    logger.warning(f"embedding 模型预热失败（下次用到时再加载）: {e}")

            threading.Thread(target=_prewarm_local_embedder, name="embedding-prewarm", daemon=True).start()
    except Exception as e:
        logger.warning(f"embedding 预热启动失败（非致命）: {e}")

    logger.info("startup complete")


# ===== 路由挂载 =====
app.include_router(knowledge_router)
app.include_router(kbs_router)
app.include_router(indexes_router)
app.include_router(qa_router)
app.include_router(feedback_router)
app.include_router(settings_router)
app.include_router(sessions_router)
app.include_router(logs_router)
app.include_router(ai_logs_router)


# ===== 数据模型 =====


class HealthResponse(BaseModel):
    status: str
    version: str
    app_name: str
    vector_db: dict
    llm: dict
    feed_folder: str
    feature_flags: dict


# ===== 健康检查 =====


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/info")
def root() -> dict:
    """API 元信息（原 / 路由让位给前端 SPA）。"""
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "/api/v1/knowledge/stats",
            "/api/v1/knowledge/scan",
            "/api/v1/knowledge/files",
            "/api/v1/knowledge/upload",
            "/api/v1/knowledge/supported-types",
            "/api/v1/qa/ask",
            "/api/v1/feedback",
            "/api/v1/feedback/pending",
            "/api/v1/feedback/approved",
            "/api/v1/feedback/{id}/review",
            "/api/v1/embedding/status",
            "/api/v1/embedding/switch",
            "/api/v1/embedding/precheck",
            "/api/v1/embedding/rebuild-and-switch",
            "/api/v1/embedding/rebuild/status",
            "/api/v1/embedding/cache-config",
        ],
    }


# ===== Embedding 管理 =====


class EmbeddingSwitchRequest(BaseModel):
    model_name: str


@app.get("/api/v1/embedding/status")
def embedding_status() -> dict:
    """查看当前 embedding 模型状态和可选模型列表。"""
    client = llm_client.get_client()
    return client.get_embedding_status()


@app.post("/api/v1/embedding/switch")
def embedding_switch(req: EmbeddingSwitchRequest) -> dict:
    """切换 embedding 模型（运行时切换，无需重启）。

    注意：切换模型后，已有向量库的维度可能不同，需要重新扫描投喂。
    前端应先调 /precheck 判断是否需要重建。
    """
    from src.knowledge import rebuild
    if rebuild.is_rebuilding():
        raise HTTPException(status_code=409, detail="向量库重建中，请稍后再试")
    client = llm_client.get_client()
    status = client.get_embedding_status()
    valid_names = [m["name"] for m in status.get("available_models", [])]
    if req.model_name not in valid_names:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {req.model_name}。可选: {valid_names}",
        )
    try:
        return client.switch_embedding_model(req.model_name)
    except llm_client.EmbeddingBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/v1/embedding/precheck")
def embedding_precheck(req: EmbeddingSwitchRequest) -> dict:
    """切换前预检：判断维度是否变化、是否需要重建、预估耗时。

    只读操作，不切换模型、不修改任何状态。
    """
    from src.knowledge import rebuild
    client = llm_client.get_client()
    status = client.get_embedding_status()
    valid_names = [m["name"] for m in status.get("available_models", [])]
    if req.model_name not in valid_names:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {req.model_name}。可选: {valid_names}",
        )
    return rebuild.precheck(req.model_name)


@app.post("/api/v1/embedding/rebuild-and-switch")
def embedding_rebuild_and_switch(req: EmbeddingSwitchRequest) -> dict:
    """切换模型并重建向量库（异步执行，配合 /rebuild/status 轮询）。

    失败自动回滚到旧模型 + 用旧模型重新 ingest。
    """
    import threading
    from src.knowledge import rebuild

    if rebuild.is_rebuilding():
        raise HTTPException(status_code=409, detail="已有重建任务进行中")

    client = llm_client.get_client()
    status = client.get_embedding_status()
    valid_names = [m["name"] for m in status.get("available_models", [])]
    if req.model_name not in valid_names:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的模型: {req.model_name}。可选: {valid_names}",
        )

    # 启动后台线程，立即返回
    thread = threading.Thread(
        target=rebuild.rebuild_and_switch,
        args=(req.model_name,),
        daemon=True,
    )
    thread.start()
    return {
        "status": "started",
        "model_name": req.model_name,
        "message": "重建已在后台启动，请通过 /api/v1/embedding/rebuild/status 查询进度",
    }


@app.get("/api/v1/embedding/rebuild/status")
def embedding_rebuild_status() -> dict:
    """查询重建进度。"""
    from src.knowledge import rebuild
    status = rebuild.get_status()
    # System settings viewers have no implicit access to private file names or error fragments.
    if accounts.enabled:
        status['current_file'] = ''
        status['stage'] = {'idle':'空闲','in_progress':'系统向量重建中','succeeded':'完成','failed':'失败，请联系服务器维护人员查看运行日志'}.get(status.get('status'),'处理中')
        if status.get('error'): status['error'] = '重建失败，详细信息保留在服务器运行日志中'
    return status


class EmbeddingCacheConfigRequest(BaseModel):
    cache_size: int = Field(ge=0, le=10, description="缓存上限：0=禁用，1-10=LRU 大小")


@app.post("/api/v1/embedding/cache-config")
def embedding_cache_config(req: EmbeddingCacheConfigRequest) -> dict:
    """修改 embedding 缓存策略（运行时生效，并写回 config.yaml）。

    - 切到 0 会立即清空所有常驻模型（释放内存）
    - 切到更小值会淘汰多出来的缓存
    """
    client = llm_client.get_client()
    return client.set_embedding_cache_size(req.cache_size)


# ===== 前端 SPA 托管 =====
# 必须放在所有 API 路由之后，否则会拦截 /api/v1/* 等请求。

# 兼容 PyInstaller 打包：检测 frozen 状态，从 _MEIPASS 找前端资源
import sys as _sys
if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
    FRONTEND_DIST = Path(_sys._MEIPASS) / "frontend" / "dist"
else:
    FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


if FRONTEND_DIST.exists():
    # 挂载静态资源目录（JS/CSS/图片等带 hash 的文件）
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str, request: Request):
        """所有未匹配的路径都尝试返回静态文件，否则回退到 index.html（React Router 接管）。"""
        # 防止误吞 API 请求
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
            raise HTTPException(status_code=404, detail="Not Found")

        # 1. 直接命中静态文件（如 favicon.ico、logo.png）
        candidate = (FRONTEND_DIST / full_path).resolve()
        if not candidate.is_relative_to(FRONTEND_DIST.resolve()):
            raise HTTPException(404, "Not found")
        if candidate.is_file():
            return FileResponse(candidate)

        # 2. 否则返回 index.html，交给 React Router 处理（/knowledge、/review 等都走这里）
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)

        raise HTTPException(status_code=404, detail="Frontend not built. Run `npm run build` in frontend/.")
else:
    @app.get("/")
    def root_no_frontend() -> dict:
        """前端未构建时的提示（P2-23：明确 HTTP 503 + 醒目错误）。"""
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={
                "error": "frontend_not_built",
                "message": "前端资源未构建，无法提供服务",
                "hint": "在 frontend/ 目录运行 `npm install && npm run build`，然后重启 API",
                "expected_path": str(FRONTEND_DIST),
            },
        )


@app.post("/api/v1/_debug/llm_chat_source")
def _debug_llm_chat_source():
    """临时调试端点（保留以便未来排查）。"""
    import inspect
    from src.core import llm_client
    src = inspect.getsource(llm_client.LLMClient.chat)
    return {"src": src, "has_v2": "V2 called" in src, "has_log_call": "_log_call" in src}


@app.on_event('shutdown')
async def stop_conversation_summary_tasks():
    from src.qa.conversation_context import shutdown
    await shutdown()
    from src.knowledge import index_tasks
    index_tasks.shutdown()
