"""Reranker：基于 cross-encoder 的二次排序。

bge-reranker-v2-m3：多语言、精度高、约 568MB。
延迟加载（第一次调用才下载/加载模型）。

与 embedding 的区别：
- embedding 是双塔结构（query / doc 独立编码），适合大规模检索
- reranker 是交互式（query + doc 拼在一起过 cross-encoder），精度更高但慢
- 典型用法：embedding 召回 top-N → reranker 重排取 top-K
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


_reranker_cache: dict[str, Any] = {}


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


def rerank(
    query: str,
    documents: list[dict[str, Any]],
    top_k: int = 5,
    model_name: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """对检索结果二次排序。

    参数：
        query: 用户问题
        documents: 检索结果列表（每项需有 "text" 字段）
        top_k: 返回前 K 条
        model_name: reranker 模型名
    返回：
        重排后的文档列表（带 rerank_score，覆盖 score 为归一化值）
    """
    if not documents:
        return []
    if len(documents) == 1:
        return documents

    model = _get_reranker(model_name)
    pairs = [(query, doc["text"]) for doc in documents]
    raw_scores = model.predict(pairs, show_progress_bar=False)

    raw_scores = np.asarray(raw_scores, dtype=float).reshape(-1)
    # bge-reranker 输出 logits，sigmoid 归一化到 [0, 1] 让 score 直观
    norm_scores = 1.0 / (1.0 + np.exp(-raw_scores))

    scored = list(zip(documents, norm_scores))
    scored.sort(key=lambda x: x[1], reverse=True)

    out = []
    for doc, score in scored[:top_k]:
        doc_copy = dict(doc)
        doc_copy["rerank_score"] = float(score)
        doc_copy["score"] = float(score)
        out.append(doc_copy)
    return out
