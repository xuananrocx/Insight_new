"""Streamlit 与 FastAPI 之间的 HTTP 客户端封装。

Streamlit 不直接访问 Chroma/SQLite，所有数据操作通过调用 FastAPI HTTP 接口完成。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st


@st.cache_resource
def _get_base_url() -> str:
    """读取 API 地址，优先用环境变量覆盖（便于容器化）。"""
    return os.getenv("API_URL", "http://localhost:8000")


def _client() -> httpx.Client:
    return httpx.Client(base_url=_get_base_url(), timeout=120.0)


def health() -> dict[str, Any]:
    try:
        with _client() as c:
            r = c.get("/health")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ===== 知识库 =====


def kb_stats() -> dict:
    with _client() as c:
        r = c.get("/api/v1/knowledge/stats")
        r.raise_for_status()
        return r.json()


def kb_files(status: str | None = None) -> list[dict]:
    params = {}
    if status:
        params["status"] = status
    with _client() as c:
        r = c.get("/api/v1/knowledge/files", params=params)
        r.raise_for_status()
        return r.json().get("files", [])


def kb_scan() -> dict:
    with _client() as c:
        r = c.post("/api/v1/knowledge/scan")
        r.raise_for_status()
        return r.json()


def kb_supported_types() -> dict:
    with _client() as c:
        r = c.get("/api/v1/knowledge/supported-types")
        r.raise_for_status()
        return r.json()


def kb_upload(file_name: str, file_bytes: bytes) -> dict:
    with _client() as c:
        files = {"file": (file_name, file_bytes)}
        r = c.post("/api/v1/knowledge/upload", files=files)
        r.raise_for_status()
        return r.json()


def kb_delete(rel_path: str) -> dict:
    with _client() as c:
        r = c.delete(f"/api/v1/knowledge/files/{rel_path}")
        if r.status_code == 404:
            return {"ok": False, "error": r.json().get("detail", "未找到")}
        r.raise_for_status()
        return r.json()


# ===== 问答 =====


def qa_ask(question: str, top_k: int | None = None, history: list[dict] | None = None) -> dict:
    payload: dict = {"question": question}
    if top_k is not None:
        payload["top_k"] = top_k
    if history:
        payload["history"] = history
    with _client() as c:
        r = c.post("/api/v1/qa/ask", json=payload)
        r.raise_for_status()
        return r.json()


# ===== 反馈 =====


def feedback_submit(payload: dict) -> dict:
    with _client() as c:
        r = c.post("/api/v1/feedback", json=payload)
        r.raise_for_status()
        return r.json()


def feedback_list(status: str = "pending") -> list[dict]:
    endpoint = "pending" if status == "pending" else "approved"
    with _client() as c:
        r = c.get(f"/api/v1/feedback/{endpoint}")
        r.raise_for_status()
        return r.json().get("items", [])


def feedback_review(feedback_id: int, decision: str, reviewer: str = "pm", note: str = "") -> dict:
    with _client() as c:
        r = c.post(
            f"/api/v1/feedback/{feedback_id}/review",
            json={"decision": decision, "reviewer": reviewer, "note": note},
        )
        if r.status_code >= 400:
            return {"ok": False, "error": r.text}
        return r.json()
