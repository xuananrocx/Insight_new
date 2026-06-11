"""FastAPI 主程序。

启动方式：
    uvicorn src.api.main:app --reload --port 8000

架构约束：
    所有数据访问（Chroma、SQLite）只在 FastAPI 进程内进行。
    Streamlit/其他客户端通过 HTTP 调用 API。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src import __version__
from src.api.routes_feedback import router as feedback_router
from src.api.routes_knowledge import router as knowledge_router
from src.api.routes_qa import router as qa_router
from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 允许 Streamlit 跨域访问（不同端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _on_startup() -> None:
    """启动时初始化数据库。"""
    metadata_db.init_db()


# ===== 路由挂载 =====
app.include_router(knowledge_router)
app.include_router(qa_router)
app.include_router(feedback_router)


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


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        app_name=settings.app_name,
        vector_db=vector_store.health_check(),
        llm=llm_client.health_check(),
        feed_folder=str(settings.feed_folder),
        feature_flags=settings.config.get("feature_flags", {}),
    )


@app.get("/")
def root() -> dict:
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
        ],
    }
