"""C2 验收：ONNX int8 vs PyTorch CrossEncoder 排序一致性对比。

用真实会话里的真实问题，走真实检索（basic 管道取 20 个融合候选），
两套后端各排一遍，对比 top-5 重合度与分数相关性。

通过标准：平均 top-5 重合度 >= 0.90。
用法（Insight 根目录）：.venv/Scripts/python.exe scripts/compare_rerank_backends.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

TOP = 5
N_QUESTIONS = 15


def collect_questions() -> list[tuple[str, str]]:
    from src.db import metadata_db
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for s in metadata_db.list_sessions(limit=200):
        kb = s.get("kb_scope")
        if not kb or kb == "default":
            continue
        detail = metadata_db.get_session(s["id"]) or {}
        for t in detail.get("turns", []):
            q = (t.get("question") or "").strip()
            if len(q) >= 6 and q not in seen:
                seen.add(q)
                out.append((q, kb))
        if len(out) >= N_QUESTIONS:
            break
    return out[:N_QUESTIONS]


def get_candidates(question: str, kb: str) -> list[dict]:
    from src.qa import rag
    from src.qa.trace import TraceCollector
    _, hits, _ = rag._run_pipeline(
        question, top_k=20, history=None, trace=TraceCollector(), kb_scope=kb, strategy="basic",
    )
    return hits


def main() -> int:
    from src.qa import reranker

    questions = collect_questions()
    print(f"[compare] 收集到 {len(questions)} 个真实问题")
    if not questions:
        print("[compare] 没有问题可测，FAIL")
        return 1

    overlaps, corrs = [], []
    for i, (q, kb) in enumerate(questions, 1):
        docs = get_candidates(q, kb)
        if len(docs) < TOP:
            print(f"[compare] {i}/{len(questions)} 候选不足({len(docs)})，跳过: {q[:30]}")
            continue
        a = reranker._rerank_torch(q, docs, 20, reranker.DEFAULT_MODEL)
        b = reranker.rerank_onnx(q, docs, 20)
        ids_a = [h["id"] for h in a[:TOP]]
        ids_b = [h["id"] for h in b[:TOP]]
        overlap = len(set(ids_a) & set(ids_b)) / TOP
        score_a = {h["id"]: h["rerank_score"] for h in a}
        score_b = {h["id"]: h["rerank_score"] for h in b}
        common = [cid for cid in score_a if cid in score_b]
        corr = float(np.corrcoef([score_a[c] for c in common], [score_b[c] for c in common])[0][1]) if len(common) > 2 else 0.0
        overlaps.append(overlap)
        corrs.append(corr)
        print(f"[compare] {i}/{len(questions)} overlap={overlap:.0%} corr={corr:+.3f} | {q[:36]}")

    mean_overlap = sum(overlaps) / len(overlaps)
    mean_corr = sum(corrs) / len(corrs)
    print(f"\n[compare] 平均 top-5 重合度: {mean_overlap:.1%} | 平均分数相关性: {mean_corr:+.3f} | n={len(overlaps)}")
    if mean_overlap >= 0.90 - 1e-9:
        print("[compare] PASS (>=90%)")
        return 0
    print("[compare] FAIL (<90%)，建议回退 fp32 ONNX 或 torch")
    return 1


if __name__ == "__main__":
    sys.exit(main())
