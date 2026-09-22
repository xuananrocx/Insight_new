"""AI 摘要 + 概念提取模块（迭代 2）。

核心思想：
- 小文档（≤ ingest.ai_summary.segment_chars，默认 3 万字）1 次 LLM 调用同时生成
  「文档摘要」+「核心概念列表」（合并 prompt，省 token）
- 大文档按章节边界分段（map-reduce）：每段先出要点笔记，再汇总成摘要 + 概念，
  保证全文覆盖（不再按固定字数截断丢内容）
- 摘要 → embedding → 写入 Chroma（chunk_type='summary'），用于"摘要增强"检索
- 概念 → 去重入 kb_concepts 表（KB 级聚合）

幂等性：
- 重复处理同一文件，会先删旧的 summary chunk + 旧的 concept 引用，再重新写入

可降级：
- LLM 失败 → document_meta 保持 processed_level='raw'，不影响主流程
"""
from __future__ import annotations
from src.core.ingest_state import TaskCancelledError


import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core import llm_client, vector_store
from src.core.config import settings
from src.db import metadata_db
from src.knowledge.chunker import Chunk

logger = logging.getLogger(__name__)


# 摘要 chunk 的 id 前缀（区别于普通 chunk）
SUMMARY_CHUNK_PREFIX = "summary"


@dataclass
class SummarizeResult:
    """summarize_and_extract 的返回结构。"""

    ok: bool = False
    summary: str = ""
    concepts: list[dict[str, str]] = field(default_factory=list)  # [{"name": "...", "type": "...", "description": "..."}]
    summary_chunk_id: str | None = None
    summary_tokens: int = 0
    model: str = ""
    error: str = ""


# ===== Prompt =====

_SYSTEM_PROMPT = """你是技术文档分析助手。你的任务是阅读文档，输出 JSON 格式的摘要 + 核心概念列表。

输出要求：
1. 必须返回严格的 JSON 格式（不要 markdown 代码块包裹）
2. JSON schema：
{
  "summary": "200-400 字的中文摘要，覆盖文档的核心目标、关键流程、重要参数/配置",
  "concepts": [
    {
      "name": "概念名（中文，简洁，例如：ERR-001 / 行情序列号 / amd.conf 配置 / 多播地址）",
      "type": "concept | error_code | config | command | component | metric | other",
      "description": "一句话解释这个概念是什么/做什么（< 60 字）"
    }
  ]
}

3. 概念数量：3-10 个，按重要性排序（最重要的在前）
4. 概念提取规则：
   - 提取"领域核心"概念，避免泛词（如"系统"、"问题"、"配置"等通用词不算）
   - 优先级：错误码 > 配置项 > 关键组件 > 重要命令 > 关键指标
   - 概念名简洁（≤ 12 字），不带量词
5. 摘要要求：
   - 用陈述句，不要列表
   - 包含关键名词/参数（这些是 RAG 检索的锚点）
   - 不写"本文档介绍了..."这种废话开头，直接陈述内容

⚠ JSON 转义规则（非常重要）：
- summary / description 等字符串值里**绝对不能**直接用英文双引号 `"`
- 需要引用名词时，用书名号《》或单引号 '' 或中文引号「」代替
- 例如：错误写法 `"summary": "文档标题为"测试"的指南"` ← 这会破坏 JSON
- 正确写法 `"summary": "文档标题为《测试》的指南"` 或 `"summary": "文档标题为'测试'的指南"`

不要输出 JSON 之外的任何文字。"""


def _segment_chars() -> int:
    """读 config: ingest.ai_summary.segment_chars（分段摘要的单段上限，字符）。

    文档不超过该值时单次调用全文直出；超过则走章节感知的 map-reduce。
    段长只影响调用粒度，不影响覆盖完整性。
    """
    cfg = settings.config.get("ingest", {}).get("ai_summary", {})
    return int(cfg.get("segment_chars", 30000))


def _split_chunks_by_sections(chunks: list[Chunk], max_chars: int) -> list[list[Chunk]]:
    """按章节边界把 chunks 聚合成段，每段大小不超过 max_chars。

    切分策略（章节感知 + 硬上限）：
    - 累积到 soft 上限（80% max_chars）后遇到章节变化 → 在章节边界切段
    - 累积到 max_chars → 强制切段（防单章超长）
    - 全程不足 max_chars → 单段（调用方走单次全文路径）
    """
    soft = int(max_chars * 0.8)
    segments: list[list[Chunk]] = []
    cur: list[Chunk] = []
    cur_size = 0
    for c in chunks:
        label = (c.metadata or {}).get("section_label") or ""
        prev_label = (cur[-1].metadata or {}).get("section_label") or "" if cur else ""
        if cur and (
            cur_size >= max_chars
            or (cur_size >= soft and label and label != prev_label)
        ):
            segments.append(cur)
            cur, cur_size = [], 0
        cur.append(c)
        cur_size += len(c.text)
    if cur:
        segments.append(cur)
    return segments


def _build_user_prompt(file_name: str, doc_text: str) -> str:
    """构造用户 prompt：包含文档名 + 文档全文（仅小文档单次调用路径使用）。"""
    return f"""请分析以下文档。

文档名: {file_name}

文档内容:
---
{doc_text}
---

请按 system 指令返回 JSON。"""


_MAP_SYSTEM_PROMPT = """你是技术文档分析助手。阅读文档的一个片段，输出该片段的要点笔记。

要求：
1. 150-300 字中文，陈述句，不列表
2. 覆盖片段内的关键流程、接口/类/函数名、参数与配置项、错误码
   （保留原文专有名词，它们是后续汇总的锚点）
3. 片段若只是目录、版权页、修订记录等无实质内容，仅输出：无实质内容

只输出笔记正文，不要其他文字。"""


def _build_user_prompt_from_segments(
    file_name: str, notes: list[tuple[str, str]]
) -> str:
    """构造 reduce 阶段的用户 prompt：分段要点笔记（已覆盖全文）。"""
    parts = [
        f"请分析以下文档。\n\n文档名: {file_name}\n\n"
        f"以下是按原文顺序整理的 {len(notes)} 段要点笔记（已覆盖文档全文）：\n"
    ]
    for i, (label, note) in enumerate(notes, 1):
        title = f"段 {i}（{label}）" if label else f"段 {i}"
        parts.append(f"\n--- {title} ---\n{note}\n")
    parts.append("\n请基于以上分段笔记，按 system 指令返回 JSON。")
    return "".join(parts)


def _map_segment(
    client: Any,
    file_name: str,
    kb_id: str,
    idx: int,
    total: int,
    segment_text: str,
) -> str:
    """map 阶段：对一个分段出要点笔记。失败返回空串（跳过该段，不影响其他段）。"""
    try:
        answer, _ = client.chat(
            [
                {"role": "system", "content": _MAP_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"文档名: {file_name}\n片段 {idx}/{total}：\n"
                        f"---\n{segment_text}\n---\n请输出要点笔记。"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=600,
            scene="summarize",
            log_meta={"kb_id": kb_id},
        )
    except Exception as e:
        logger.warning(f"[ai_summary] map 段 {idx}/{total} 失败: {e}")
        return ""
    note = answer.strip()
    if note.startswith("无实质内容"):
        return ""
    return note


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """解析 LLM 返回的 JSON（容错：去 markdown 代码块 + 标准解析 → json_repair fallback）。"""
    text = raw.strip()
    # 去 markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 找首个 { 到匹配的 }
    start = text.find("{")
    if start < 0:
        raise ValueError(f"LLM 输出无 JSON 起始符: {raw[:200]}")
    # 简单括号配对（不处理嵌套字符串里的 {}）
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise ValueError(f"LLM 输出 JSON 不完整: {raw[:200]}")

    json_text = text[start:end]
    # 第一层：标准 json.loads
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        pass
    # 第二层：json_repair（处理未转义引号、尾随逗号、单引号等常见 LLM 错误）
    try:
        from json_repair import repair_json
        repaired = repair_json(json_text, return_objects=True)
        if isinstance(repaired, dict):
            return repaired
    except Exception as e:
        logger.warning(f"json_repair 也失败了: {e}")
    raise ValueError(f"JSON 解析失败（标准 + repair 都不行）: {raw[:200]}")


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文按 1.5 字/token，英文按 4 字符/token）。"""
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4)


def _make_summary_chunk_id(file_id: int, content_hash: str) -> str:
    """稳定的 summary chunk id（含 file_id + hash）。"""
    import hashlib
    raw = f"{SUMMARY_CHUNK_PREFIX}|{file_id}|{content_hash}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def summarize_and_extract(
    *,
    file_id: int,
    kb_id: str,
    file_name: str,
    chunks: list[Chunk],
    content_hash: str,
    collection_name: str,
    source_path: str,
    check_cancel=None,
) -> SummarizeResult:
    """对单个文档跑 LLM 摘要 + 概念提取，并写入数据库 + 向量库。

    参数：
        file_id: knowledge_files.id
        kb_id: 所属 KB
        file_name: 文件名（用于 prompt）
        chunks: 已切好的 chunk 列表（用来拼成"文档全文"）
        content_hash: 文件 hash（用于生成稳定的 summary chunk id）
        collection_name: 该 KB 的 Chroma collection 名
        source_path: 文件绝对路径（写入 chunk metadata）

    返回：SummarizeResult
    """
    result = SummarizeResult()
    check_cancel = check_cancel or (lambda: None)
    check_cancel()

    if not chunks:
        result.error = "无 chunks 可分析"
        return result

    # 1. 调 LLM：小文档单次全文直出；大文档按章节分段 map-reduce
    try:
        client = llm_client.get_client()
        segments = _split_chunks_by_sections(chunks, _segment_chars())
        if len(segments) <= 1:
            doc_text = "\n\n".join(c.text for c in chunks)
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(file_name, doc_text)},
            ]
        else:
            logger.info(
                f"[ai_summary] file_id={file_id} 全文超段长上限，"
                f"按章节分 {len(segments)} 段 map-reduce"
            )
            notes: list[tuple[str, str]] = []
            for i, seg in enumerate(segments, 1):
                label = (seg[0].metadata or {}).get("section_label") or ""
                check_cancel()
                note = _map_segment(
                    client, file_name, kb_id, i, len(segments),
                    "\n\n".join(c.text for c in seg),
                )
                check_cancel()
                if note:
                    notes.append((label, note))
            if not notes:
                result.error = "全部分段摘要（map）均失败"
                return result
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt_from_segments(file_name, notes)},
            ]
        check_cancel()
        raw_answer, used_provider = client.chat(
            messages,
            temperature=0.1,  # 摘要要稳定，低温度
            max_tokens=1500,
            scene="summarize",
            log_meta={"kb_id": kb_id},
        )
    except TaskCancelledError:
        raise
    except Exception as e:
        result.error = f"LLM 调用失败: {e}"
        logger.warning(f"[ai_summary] file_id={file_id} LLM 失败: {e}")
        return result

    check_cancel()
    # 2. 解析 JSON
    try:
        parsed = _parse_llm_response(raw_answer)
    except TaskCancelledError:
        raise
    except Exception as e:
        result.error = f"LLM 输出解析失败: {e}"
        logger.warning(f"[ai_summary] file_id={file_id} JSON 解析失败: {e}; raw={raw_answer[:200]}")
        return result

    summary = (parsed.get("summary") or "").strip()
    raw_concepts = parsed.get("concepts") or []
    if not summary:
        result.error = "LLM 返回的 summary 为空"
        return result

    # 规范化概念列表
    clean_concepts: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for c in raw_concepts[:15]:  # 最多取 15 个
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name or len(name) > 30:
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        clean_concepts.append({
            "name": name,
            "type": str(c.get("type") or "concept").strip()[:30],
            "description": str(c.get("description") or "").strip()[:200],
        })

    result.summary = summary
    result.concepts = clean_concepts
    result.model = used_provider
    result.summary_tokens = _estimate_tokens(summary)

    # 3. 写摘要向量（chunk_type='summary'）
    summary_chunk_id = _make_summary_chunk_id(file_id, content_hash)
    try:
        embeddings, embed_provider = llm_client.get_client().embed([summary])
        check_cancel()
        summary_embedding = embeddings[0]
        summary_meta = {
            "content_hash": content_hash,
            "file_id": file_id,
            "embedding_provider": embed_provider,
            "kb_id": kb_id,
            "chunk_type": "summary",  # 关键标记：检索时可过滤
            "source_path": source_path,
            "source_name": file_name,
            "section_label": f"[摘要] {file_name}",
            "title": file_name,
        }
        vector_store.upsert_chunks(
            chunks=[{
                "id": summary_chunk_id,
                "text": summary,
                "embedding": summary_embedding,
                "metadata": summary_meta,
            }],
            collection_name=collection_name,
        )
        result.summary_chunk_id = summary_chunk_id
    except TaskCancelledError:
        raise
    except Exception as e:
        result.error = f"摘要向量化/入库失败: {e}"
        logger.warning(f"[ai_summary] file_id={file_id} 摘要入库失败: {e}")
        return result

    # 4. 概念入 kb_concepts 表（去重 + 聚合）
    try:
        for concept in clean_concepts:
            metadata_db.upsert_kb_concept(
                kb_id=kb_id,
                concept_name=concept["name"],
                concept_type=concept["type"],
                description=concept["description"],
                source_file_id=file_id,
            )
    except TaskCancelledError:
        raise
    except Exception as e:
        # 概念入库失败不影响摘要（摘要已成功）
        logger.warning(f"[ai_summary] file_id={file_id} 概念入库部分失败: {e}")

    # 5. 更新 document_meta
    try:
        metadata_db.upsert_document_meta(
            file_id=file_id,
            kb_id=kb_id,
            processed_level="summarized",
            summary=summary,
            summary_model=used_provider,
            summary_tokens=result.summary_tokens,
            concepts_extracted=len(raw_concepts),
            concepts_count=len(clean_concepts),
        )
    except TaskCancelledError:
        raise
    except Exception as e:
        logger.warning(f"[ai_summary] file_id={file_id} document_meta 更新失败: {e}")

    result.ok = True
    return result


def remove_summary_data(file_id: int, kb_id: str, content_hash: str) -> None:
    """清理某文件的 AI 摘要数据（重新投喂/删除时调用）。

    - 删 Chroma 里的 summary chunk
    - 从 kb_concepts.source_file_ids_json 移除该 file_id
    - 不删 kb_concepts 行本身（保留概念，等其他文件再引用时再聚合）
    """
    try:
        kb_row = metadata_db.get_kb(kb_id)
        collection_name = kb_row["collection_name"] if kb_row else vector_store.COLLECTION_NAME
        summary_chunk_id = _make_summary_chunk_id(file_id, content_hash)
        vector_store.delete_chunks([summary_chunk_id], collection_name=collection_name)
    except Exception as e:
        logger.warning(f"[ai_summary] 删 summary chunk 失败 file_id={file_id}: {e}")

    try:
        metadata_db.remove_file_from_concepts(file_id)
    except Exception as e:
        logger.warning(f"[ai_summary] 移除 concept 引用失败 file_id={file_id}: {e}")


# ===== KB 级全局摘要（迭代 6）=====

_GLOBAL_SUMMARY_SYSTEM_PROMPT = """你是知识库总览助手。基于多个文档摘要，输出该知识库的整体概述。

要求：
1. 输出 400-600 字的中文段落（不要用列表）
2. 覆盖：知识库主题、核心模块/组件、关键流程、重要参数/概念
3. 保留专有名词（错误码、配置项、命令名等），这些是检索锚点
4. 不写"本知识库介绍了..."这种废话开头，直接陈述
5. 突出文档之间的关联性（共享的概念、跨文档的流程）

只输出概述正文，不要其他文字。"""


def _build_global_summary_user_prompt(kb_name: str, doc_summaries: list[dict]) -> str:
    """拼接文档摘要为输入。"""
    parts = [f"知识库名称: {kb_name}\n"]
    parts.append(f"包含 {len(doc_summaries)} 个文档的摘要：\n")
    for i, s in enumerate(doc_summaries, 1):
        parts.append(f"\n--- 文档 {i}: {s['title']} ---\n{s['summary']}\n")
    parts.append("\n请基于以上文档摘要，生成该知识库的整体概述。")
    return "".join(parts)


@dataclass
class GlobalSummaryResult:
    ok: bool = False
    summary: str = ""
    model: str = ""
    tokens: int = 0
    doc_count: int = 0
    error: str = ""


def _fetch_chunks_by_ids(collection_name: str, chunk_ids: list[str]) -> list[Chunk]:
    """从向量库按 chunk_id 捞回文本与 metadata（补摘要用，保持原 chunk 顺序）。"""
    collection = vector_store.get_or_create_collection(collection_name)
    res = collection.get(ids=chunk_ids, include=["documents", "metadatas"])
    by_id = {
        cid: (doc, meta)
        for cid, doc, meta in zip(
            res.get("ids") or [],
            res.get("documents") or [],
            res.get("metadatas") or [],
        )
    }
    out: list[Chunk] = []
    for cid in chunk_ids:
        pair = by_id.get(cid)
        if pair and pair[0]:
            out.append(Chunk(text=pair[0], metadata=pair[1] or {}))
    return out


def _backfill_doc_summaries(
    kb_id: str, kb_row: dict, *, skip_file_ids: set[int]
) -> tuple[int, int]:
    """为 KB 下缺摘要的已入库文档现场生成文档级摘要。

    投喂时若 ingest.ai_summary 未启用，文档不会带摘要；这里按 knowledge_files
    存的 chunk_ids 从向量库取回文本，复用 summarize_and_extract 补齐，
    让事后手动生成 KB 摘要不依赖投喂时的开关。

    返回：(成功数, 失败数)
    """
    ok_cnt = fail_cnt = 0
    files = metadata_db.list_files_in_kb(kb_id, status="done")
    collection_name = kb_row["collection_name"]
    for f in files:
        if f["id"] in skip_file_ids:
            continue
        chunk_ids = json.loads(f.get("chunk_ids_json") or "[]")
        chunks = _fetch_chunks_by_ids(collection_name, chunk_ids) if chunk_ids else []
        if not chunks:
            fail_cnt += 1
            logger.warning(
                f"[global_summary] 补摘要跳过 file_id={f['id']}：向量库无 chunk 文本"
            )
            continue
        source_path = f.get("absolute_path") or f["relative_path"]
        res = summarize_and_extract(
            file_id=f["id"],
            kb_id=kb_id,
            file_name=Path(source_path).name,
            chunks=chunks,
            content_hash=f["content_hash"],
            collection_name=collection_name,
            source_path=source_path,
        )
        if res.ok:
            ok_cnt += 1
        else:
            fail_cnt += 1
            logger.warning(f"[global_summary] 补摘要失败 file_id={f['id']}: {res.error}")
    return ok_cnt, fail_cnt


def generate_kb_global_summary(kb_id: str, *, force: bool = False) -> GlobalSummaryResult:
    """为整个 KB 生成全局摘要。

    流程：
    1. 拉 KB 下所有 document_meta.summary
    2. 缺摘要的文档从向量库捞回 chunk 文本，现场补文档级摘要
    3. 拼成大文本，1 次 LLM 调用生成全局摘要
    4. 写到 kbs.global_summary

    参数：
        force: True 时强制重新生成（即使已有）

    返回：GlobalSummaryResult
    """
    result = GlobalSummaryResult()

    # 1. 检查现有
    if not force:
        existing = metadata_db.get_kb_global_summary(kb_id)
        if existing:
            result.ok = True
            result.summary = existing["summary"]
            result.model = existing["model"] or ""
            result.tokens = existing["tokens"] or 0
            result.error = "already_exists"
            return result

    kb_row = metadata_db.get_kb(kb_id)
    if kb_row is None:
        result.error = f"KB 不存在: {kb_id}"
        return result
    kb_name = kb_row["name"]

    # 2. 缺摘要的文档现场补齐，再拉全量摘要
    # force 时连文档摘要一起重生成（旧的可能是截断版/旧模型产物）
    all_metas = metadata_db.list_document_meta_by_kb(kb_id)
    summarized_ids = set() if force else {
        m["file_id"] for m in all_metas if m.get("summary")
    }
    ok_cnt, fail_cnt = _backfill_doc_summaries(kb_id, kb_row, skip_file_ids=summarized_ids)
    if ok_cnt or fail_cnt:
        logger.info(
            f"[global_summary] kb={kb_id} 补齐文档摘要 {ok_cnt} 份（失败 {fail_cnt}）"
        )
        all_metas = metadata_db.list_document_meta_by_kb(kb_id)

    doc_summaries = [
        {"title": f"file #{m['file_id']}", "summary": m["summary"]}
        for m in all_metas
        if m.get("summary")
    ]
    if not doc_summaries:
        result.error = "KB 无文档摘要（已尝试自动补摘要：无已入库文档或 LLM 调用失败，详见后端日志）"
        return result

    # 限制最多 30 个文档（避免 prompt 过长）
    if len(doc_summaries) > 30:
        doc_summaries = doc_summaries[:30]

    # 3. 调 LLM
    try:
        client = llm_client.get_client()
        messages = [
            {"role": "system", "content": _GLOBAL_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": _build_global_summary_user_prompt(kb_name, doc_summaries)},
        ]
        raw_answer, used_provider = client.chat(
            messages,
            temperature=0.1,
            max_tokens=1500,
            scene="summarize",
            log_meta={"kb_id": kb_id},
        )
    except Exception as e:
        result.error = f"LLM 调用失败: {e}"
        logger.warning(f"[global_summary] kb={kb_id} LLM 失败: {e}")
        return result

    summary = raw_answer.strip()
    if not summary or len(summary) < 50:
        result.error = f"LLM 输出过短: {len(summary)} 字"
        return result

    # 4. 写入 db（带文件快照，供摘要过期检测）
    try:
        metadata_db.update_kb_global_summary(
            kb_id=kb_id,
            summary=summary,
            model=used_provider,
            tokens=_estimate_tokens(summary),
            snapshot=metadata_db.list_kb_file_hashes(kb_id),
        )
    except Exception as e:
        result.error = f"写入 db 失败: {e}"
        return result

    result.ok = True
    result.summary = summary
    result.model = used_provider
    result.tokens = _estimate_tokens(summary)
    result.doc_count = len(doc_summaries)
    return result
