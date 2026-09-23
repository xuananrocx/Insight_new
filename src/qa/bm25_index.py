"""Incremental keyword search, retaining the public BM25 API for callers.

Legacy JSON is imported once by lexical_store; no per-document global rebuild.
"""
import re
from src.core.ingest_state import publication_guard

def _tokenize(text: str) -> list[str]:
    """中文分词 + 过滤空白。"""
    import jieba
    tokens: list[str] = []
    for tok in jieba.lcut_for_search(text):
        tok = tok.strip()
        if not tok:
            continue
        if tok in {" ", "\n", "\t", "\r", "。", "，", "、", "；"}:
            continue
        tokens.append(tok.lower())
    return tokens


def identifiers(text: str) -> list[str]:
    """Preserve API names, error codes, configuration keys and version numbers."""
    return list(dict.fromkeys(re.findall(r"[A-Za-z0-9_]+(?:[.:-][A-Za-z0-9_]+)*", text.lower())))[:20]


from src.qa import lexical_store as _incremental

def add_chunks(chunks):
    if chunks:
        _incremental.add(chunks)

def remove_chunks(chunk_ids):
    if chunk_ids:
        _incremental.remove(chunk_ids)

def clear():
    _incremental.clear()

@publication_guard
def query(query_text, top_k=20, kb_id=None):
    return _incremental.query(query_text,top_k,kb_id)

@publication_guard
def query_enhanced(query_text, top_k=20, kb_id='default'):
    return _incremental.query(query_text,top_k,kb_id,enhanced=True)

def stats():
    return _incremental.stats()
