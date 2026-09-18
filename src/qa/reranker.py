"""Reranker：基于 cross-encoder 的二次排序。

后端两套，按 qa.rerank.backend 配置选择：
- onnx（C2 方案）：onnx-community/bge-reranker-v2-m3-ONNX 的动态 int8 量化版，
  CPU 上比 PyTorch CrossEncoder 快一个数量级（实测 0.14s/对 vs 1.7s/对）。
  模型缺失时首用自动下载（也可用 scripts/download_reranker_onnx.py 手动预下载）。
- torch（原有路径）：sentence_transformers CrossEncoder，精度基准，兜底。

与 embedding 的区别：
- embedding 是双塔结构（query / doc 独立编码），适合大规模检索
- reranker 是交互式（query + doc 拼在一起过 cross-encoder），精度更高但慢
- 典型用法：embedding 召回 top-N → reranker 重排取 top-K
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
# 与 CrossEncoder 的默认截断一致，保证两后端可比
_MAX_LENGTH = 512

_ONNX_REPO = "onnx-community/bge-reranker-v2-m3-ONNX"
_ONNX_FILES = [
    "onnx/model_int8.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]

_reranker_cache: dict[str, Any] = {}
_onnx_cache: dict[str, tuple[Any, Any]] = {}


def _onnx_model_dir() -> Path:
    from src.core.config import settings
    return settings.get_path("metadata_db").parent / "models" / "reranker" / "bge-reranker-v2-m3-int8"


def _get_reranker(model_name: str):
    """延迟加载 reranker 模型（按 model_name 缓存）。"""
    if model_name in _reranker_cache:
        return _reranker_cache[model_name]
    from sentence_transformers import CrossEncoder
    logger.info(f"加载 reranker 模型: {model_name}（首次需下载约 568MB）")
    model = CrossEncoder(model_name, device="cpu")
    _reranker_cache[model_name] = model
    logger.info("reranker 加载完成")
    return model


def _download_onnx(mdir: Path) -> None:
    """首用下载 ONNX 模型（打包版/新机器）。失败向上抛，由 auto 后端回退 torch。"""
    from huggingface_hub import hf_hub_download

    mdir.mkdir(parents=True, exist_ok=True)
    logger.info(f"ONNX reranker 模型缺失，首用下载约 150MB: {_ONNX_REPO}")
    for f in _ONNX_FILES:
        hf_hub_download(repo_id=_ONNX_REPO, filename=f, local_dir=str(mdir))


def _get_onnx(model_dir: str | Path | None = None) -> tuple[Any, Any]:
    """延迟加载 ONNX int8 reranker，返回 (tokenizer, InferenceSession)。"""
    key = str(model_dir or _onnx_model_dir())
    if key in _onnx_cache:
        return _onnx_cache[key]
    import onnxruntime as ort
    from transformers import AutoTokenizer

    mdir = Path(key)
    model_file = mdir / "onnx" / "model_int8.onnx"
    if not model_file.exists():
        _download_onnx(mdir)
    if not model_file.exists():
        raise FileNotFoundError(f"ONNX reranker 模型下载后仍不存在: {model_file}")
    logger.info(f"加载 reranker ONNX int8: {model_file}")
    tok = AutoTokenizer.from_pretrained(str(mdir))
    sess = ort.InferenceSession(str(model_file), providers=["CPUExecutionProvider"])
    _onnx_cache[key] = (tok, sess)
    logger.info("reranker ONNX int8 加载完成")
    return tok, sess


def rerank(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int = 5,
    model_name: str = DEFAULT_MODEL,
    backend: str = "auto",
) -> list[dict[str, Any]]:
    """对检索结果二次排序。

    参数：
        query: 用户问题
        documents: 检索结果列表（每项需有 "text" 字段）
        top_k: 返回前 K 条
        model_name: torch 后端的模型名
        backend: "auto"（优先 ONNX，失败回退 torch）/ "onnx" / "torch"
    返回：
        重排后的文档列表（带 rerank_score，覆盖 score 为归一化值）
    """
    if not documents:
        return []
    if len(documents) == 1:
        return documents

    if backend in ("auto", "onnx"):
        try:
            return rerank_onnx(query, documents, top_k)
        except Exception as e:
            if backend == "onnx":
                raise
            logger.warning(f"ONNX rerank 失败，回退 CrossEncoder: {e}")
    return _rerank_torch(query, documents, top_k, model_name)


def rerank_onnx(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int = 5,
    model_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """ONNX int8 后端重排。输出结构与 _rerank_torch 完全一致。"""
    tok, sess = _get_onnx(model_dir)
    texts = [doc["text"] for doc in documents]
    enc = tok(
        [query] * len(texts),
        texts,
        padding=True,
        truncation=True,
        max_length=_MAX_LENGTH,
        return_tensors="np",
    )
    out = sess.run(
        None,
        {
            "input_ids": enc["input_ids"].astype(np.int64),
            "attention_mask": enc["attention_mask"].astype(np.int64),
        },
    )
    raw_scores = np.asarray(out[0], dtype=float).reshape(-1)
    # bge-reranker 输出 logits，sigmoid 归一化到 [0, 1]（与 torch 后端口径一致）
    norm_scores = 1.0 / (1.0 + np.exp(-raw_scores))
    return _sorted_top_k(documents, norm_scores, top_k)


def _rerank_torch(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int,
    model_name: str,
) -> list[dict[str, Any]]:
    model = _get_reranker(model_name)
    pairs = [(query, doc["text"]) for doc in documents]
    raw_scores = model.predict(pairs, show_progress_bar=False)

    raw_scores = np.asarray(raw_scores, dtype=float).reshape(-1)
    # bge-reranker 输出 logits，sigmoid 归一化到 [0, 1] 让 score 直观
    norm_scores = 1.0 / (1.0 + np.exp(-raw_scores))
    return _sorted_top_k(documents, norm_scores, top_k)


def _sorted_top_k(documents: list[dict[str, Any]], norm_scores: np.ndarray, top_k: int) -> list[dict[str, Any]]:
    scored = list(zip(documents, norm_scores))
    scored.sort(key=lambda x: x[1], reverse=True)

    out = []
    for doc, score in scored[:top_k]:
        doc_copy = dict(doc)
        doc_copy["rerank_score"] = float(score)
        doc_copy["score"] = float(score)
        out.append(doc_copy)
    return out
