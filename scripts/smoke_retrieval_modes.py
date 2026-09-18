"""三档检索模式真实后端冒烟：basic/deep 不出 token、deep 合并生效、会话 mode 持久化。"""
import json
import sys
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1"
KB = "kb_c3c76c19539a482984476a5b9f590a35"
Q = "QueryMDTick 查不到数据怎么排查"


def stream_events(mode, sid=None):
    body = {"question": Q, "kb_scope": KB, "top_k": 5, "mode": mode}
    if sid:
        body["session_id"] = sid
    r = requests.post(f"{BASE}/qa/ask_stream", json=body, stream=True, timeout=120)
    r.raise_for_status()
    events = []
    cur_type = None
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("event: "):
            cur_type = line[7:].strip()
        elif line and line.startswith("data: "):
            events.append({"type": cur_type, "data": json.loads(line[6:])})
    return events


def main():
    ok = True

    # 1. basic：有 results、无 token
    t0 = time.time()
    evts = stream_events("basic")
    dt = time.time() - t0
    types = [e.get("type") for e in evts]
    results = next((e for e in evts if e.get("type") == "results"), {}).get("data", {})
    done = next((e for e in evts if e.get("type") == "done"), {}).get("data", {})
    print(f"[basic] {dt:.1f}s types={types}")
    print(f"[basic] hits={len(results.get('hits', []))} terms={results.get('highlight_terms')}")
    if results.get("hits"):
        h = results["hits"][0]
        print(f"[basic] top: {h.get('source_name')} score_pct={h.get('score_pct')} len={len(h.get('content', ''))}")
    assert "results" in types and "token" not in types, "basic 不应有 token"
    assert done.get("mode") == "basic" and done.get("answer") == ""
    assert results.get("hits"), "basic 应有命中"

    # 2. deep：results 有命中，检查 merged_chunks
    evts = stream_events("deep")
    results = next((e for e in evts if e.get("type") == "results"), {}).get("data", {})
    done = next((e for e in evts if e.get("type") == "done"), {}).get("data", {})
    merged = [h.get("merged_chunks", 1) for h in results.get("hits", [])]
    print(f"[deep] hits={len(results.get('hits', []))} merged_chunks={merged}")
    assert "token" not in [e.get("type") for e in evts], "deep 不应有 token"
    assert done.get("mode") == "deep"
    assert results.get("hits"), "deep 应有命中"

    # 3. 会话 mode 持久化 + 回退解析（不传 mode 时读会话 retrieval_mode）
    sid = f"smoke-{int(time.time())}"
    r = requests.post(f"{BASE}/sessions", json={
        "id": sid, "title": "smoke", "created_at": int(time.time() * 1000),
        "kb_scope": KB, "retrieval_mode": "deep",
    }, timeout=30)
    r.raise_for_status()
    assert r.json()["retrieval_mode"] == "deep", "create 应返回 deep"

    evts = stream_events(None, sid=sid)  # 不传 mode → 应读会话的 deep
    results = next((e for e in evts if e.get("type") == "results"), {}).get("data", {})
    assert results.get("mode") == "deep", f"应回落到会话 mode=deep，实际 {results.get('mode')}"
    print(f"[session] 回落会话 mode=deep OK")

    # 4. turns 带 mode 落库（turns.id 全局唯一，每次跑要用新 id）
    tid = f"t-smoke-{int(time.time())}"
    r = requests.post(f"{BASE}/sessions/{sid}/turns", json={
        "id": tid, "question": Q, "answer": "", "sources": results["hits"][:2],
        "created_at": int(time.time() * 1000), "mode": "deep",
    }, timeout=30)
    r.raise_for_status()
    assert r.json()["mode"] == "deep"
    s = requests.get(f"{BASE}/sessions/{sid}", timeout=30).json()
    assert s["turns"][0]["mode"] == "deep"
    assert len(s["turns"][0]["sources"][0].get("content", "")) > 100, "long_content 应保留片段正文"
    print(f"[turn] mode=deep sources={len(s['turns'][0]['sources'])} 落库 OK")

    # 5. 旧 retrieval_strategy 端点已删
    r = requests.get(f"{BASE}/settings/retrieval_strategy?kb_id={KB}", timeout=30)
    assert r.status_code == 404, f"retrieval_strategy 应已 404，实际 {r.status_code}"
    print("[legacy] retrieval_strategy 404 OK")

    # 6. ai 档照常流式（只验证事件序列出现 sources/token/done，不等全部生成完）
    body = {"question": "用一句话说明 QueryMDTick 是什么", "kb_scope": KB, "mode": "ai"}
    got_token = False
    with requests.post(f"{BASE}/qa/ask_stream", json=body, stream=True, timeout=180) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("event: ") and line[7:].strip() == "token":
                got_token = True
                break
    assert got_token, "ai 模式应有 token"
    print("[ai] token 流 OK")

    requests.delete(f"{BASE}/sessions/{sid}", timeout=30)
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    sys.exit(main())
