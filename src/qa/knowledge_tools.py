"""Read-only, request-scoped knowledge tools. IDs and versions come from storage."""
from __future__ import annotations

import json
import logging
import re
from pathlib import PurePosixPath

from fastapi import HTTPException

from src.core import accounts, vector_store
from src.db import metadata_db as db
from src.qa import retrieval, bm25_index
from src.qa.trace import TraceCollector, PipelineCancelled

logger = logging.getLogger(__name__)


def definition(name, description, properties=None, required=None):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}


TOOLS = [
    definition("get_kb_overview", "读取当前知识库背景和入库状态；概览不是可引用的原文证据。"),
    definition("search_knowledge", "在当前知识库语义和关键词检索。返回原文证据及引用编号；可换词补查或按 document_id 查指定文档。",
               {"query": {"type": "string", "minLength": 1, "maxLength": 1000}, "document_id": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}}, ["query"]),
    definition("list_documents", "按文件名查找文档，包含入库状态。标题不能当作正文引用。支持分页。",
               {"query": {"type": "string", "maxLength": 200}, "offset": {"type": "integer", "minimum": 0}}),
    definition("get_document_outline", "获取文档章节、版本和索引完整性。章节编号用于 read_document；目录不是正文证据。",
               {"document_id": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0}}, ["document_id"]),
    definition("read_document", "按章节读取已入库原文（每页最多6000字符），用 next_offset 继续。需传 outline/search 返回的版本；文档更新后重新查。",
               {"document_id": {"type": "integer", "minimum": 1}, "version": {"type": "string", "maxLength": 128}, "section": {"type": "integer", "minimum": 0}, "offset": {"type": "integer", "minimum": 0}}, ["document_id", "version"]),
]
LABELS = {"get_kb_overview": "查看知识库背景", "search_knowledge": "检索知识库", "list_documents": "查找文档", "get_document_outline": "查看文档目录", "read_document": "阅读文档原文"}


class KnowledgeTools:
    def __init__(self, kb_id, check_cancel=lambda: None, *, max_chars=48000, top_k=8):
        self.kb_id = kb_id
        self.check_cancel = check_cancel
        self.max_chars = max_chars
        self.top_k = min(8, max(1, top_k or 8))
        self.hits: list[dict] = []
        self.cache: dict[str, dict] = {}
        self.used_chars = 0

    def check(self):
        self.check_cancel()
        if accounts.enabled:
            accounts.require_kb(self.kb_id)
            accounts.resolve_provider(accounts.selected_provider.get())

    def files(self):
        self.check()
        return db.list_files_in_kb(self.kb_id)

    def file(self, file_id):
        result = next((f for f in self.files() if f["id"] == file_id), None)
        if result is None:
            raise ValueError("当前知识库中没有此文档")
        return result

    @staticmethod
    def brief(f):
        return {"document_id": f["id"], "name": f["relative_path"], "version": f["content_hash"], "status": f["status"]}

    @staticmethod
    def ids(f):
        return json.loads(f.get("chunk_ids_json") or "[]")

    def collection(self):
        self.check()
        kb = db.get_kb(self.kb_id)
        if not kb:
            raise ValueError("知识库不存在")
        # Unlike get_chunks_by_ids, do not silently translate an unavailable index to no results.
        return vector_store.get_chroma_client().get_collection(kb["collection_name"])

    def live_chunks(self, ids):
        if not ids:
            return {}
        result = self.collection().get(ids=ids, include=["documents", "metadatas"])
        self.check()
        return {cid: {**(meta or {}), "id": cid, "text": text or ""}
                for cid, text, meta in zip(result["ids"], result["documents"], result["metadatas"])}

    def document_chunks(self, f):
        if f["status"] != "done":
            raise ValueError("文档尚未成功入库，无法读取；这不代表原文件没有内容")
        ids = self.ids(f)
        if not ids:
            raise ValueError("文档未解析出可读文字，可能需要 OCR 或重新导入")
        # Stored IDs already have file-wide order, also valid for legacy chunks without section metadata.
        chunks = []
        for start in range(0, min(len(ids), 10000), 500):
            live = self.live_chunks(ids[start:start + 500])
            chunks.extend(live[cid] for cid in ids[start:start + 500] if cid in live and live[cid].get("chunk_type") != "summary")
        if self.file(f["id"])["content_hash"] != f["content_hash"]:
            raise ValueError("文档版本已更新，请重新获取目录")
        if not chunks:
            raise ValueError("文档索引缺失，请重新导入")
        return chunks, len(chunks) != len(ids)

    def overview(self):
        files = self.files()
        kb = db.get_kb(self.kb_id)
        if not kb:
            raise ValueError("知识库不存在")
        summary = db.get_kb_global_summary(self.kb_id)
        current = {str(f["id"]): f["content_hash"] for f in files if f["status"] == "done"}
        fresh = bool(summary and summary.get("snapshot") == current)
        return {"name": kb["name"], "description": (kb.get("description") or "")[:2000],
                "documents": len(files), "ready": len(current), "not_ready": len(files) - len(current),
                "summary": summary["summary"][:3000] if fresh else None,
                "summary_status": "current_background_only" if fresh else "missing_or_stale",
                "note": "概览仅提供背景。回答知识库事实应检索或阅读原文。"}

    def evidence(self, hit, f, text=None, *, section=None, offset=0):
        self.check()
        text = (text if text is not None else hit["text"][:2500]).strip()
        section = section if section is not None else hit.get("section_index", 0)
        key = (f["id"], f["content_hash"], hit["id"], section, offset, text)
        for i, prior in enumerate(self.hits):
            if prior["_key"] == key:
                return self.public_evidence(prior, i + 1)
        remaining = self.max_chars - self.used_chars
        if remaining <= 0:
            return {"error": "evidence_budget", "message": "原文读取预算已用完，请利用已读证据回答"}
        text = text[:remaining]
        name = PurePosixPath(f["relative_path"].replace("\\", "/")).name
        saved = {**hit, "_key": key, "text": text, "file_id": f["id"], "content_hash": f["content_hash"],
                 "source_path": f["relative_path"], "source_name": name, "title": hit.get("title") or name,
                 "file_type": f.get("file_type", ""), "section_index": section, "offset": offset,
                 "_chunk_ids": hit.get("_chunk_ids", [hit["id"]])}
        self.hits.append(saved)
        self.used_chars += len(text)
        return self.public_evidence(saved, len(self.hits))

    @staticmethod
    def public_evidence(hit, number):
        return {"citation": number, "document_id": hit["file_id"], "name": hit["source_name"], "version": hit["content_hash"],
                "section": hit["section_index"], "section_label": hit.get("section_label", ""),
                "page": hit.get("page"), "offset": hit["offset"], "text": hit["text"]}

    def search(self, query, document_id=None, limit=None):
        files = self.files()
        limit = min(limit or self.top_k, self.top_k)
        note = None
        if document_id is not None:
            f = self.file(document_id)
            chunks, incomplete = self.document_chunks(f)
            terms = retrieval._terms(query)
            hits = sorted(chunks, key=lambda h: len(terms & retrieval._terms(h["text"])), reverse=True)
            hits = [h for h in hits if terms & retrieval._terms(h["text"])][:limit]
            note = "文档内关键词检索" + ("；索引不完整" if incomplete else "")
        else:
            trace = TraceCollector()
            trace.cancel_check = self.check
            try:
                hits, _ = retrieval.search(query, limit, self.kb_id, trace, deep=False)
            except (HTTPException, PipelineCancelled):
                raise
            except Exception:
                self.check()
                logger.warning("Agent semantic search unavailable; trying lexical index", exc_info=True)
                hits = bm25_index.query_enhanced(query, limit, self.kb_id)
                note = "语义检索暂不可用，本次仅使用关键词检索；空结果不代表库内没有资料"
        # Authoritative membership uses current file records, not possibly stale chunk metadata.
        owners = {cid: f for f in files if f["status"] == "done" for cid in self.ids(f)}
        ids = [h["id"] for h in hits if h.get("id") in owners]
        live = self.live_chunks(ids)
        result = []
        for h in hits:
            cid = h.get("id")
            if cid not in live or live[cid].get("chunk_type") == "summary":
                continue
            f = owners[cid]
            if self.file(f["id"])["content_hash"] != f["content_hash"]:
                continue
            result.append(self.evidence({**h, **live[cid]}, f))
        return {"evidence": result, "note": note, "status": "found" if result else "no_match"}

    @staticmethod
    def sections(chunks):
        groups = {}
        for c in chunks:
            section = c.get("section_index", 0)
            if not isinstance(section, int) or section < 0:
                section = 0
            groups.setdefault(section, []).append(c)
        return groups

    def outline(self, document_id, offset=0):
        f = self.file(document_id)
        chunks, incomplete = self.document_chunks(f)
        sections = [{"section": index, "label": group[0].get("section_label", ""), "chunks": len(group)}
                    for index, group in self.sections(chunks).items()]
        return {**self.brief(f), "sections": sections[offset:offset + 60],
                "next_offset": offset + 60 if offset + 60 < len(sections) else None,
                "incomplete": incomplete, "legacy_order": any("section_index" not in c for c in chunks)}

    def read(self, document_id, version, section=0, offset=0):
        f = self.file(document_id)
        if version != f["content_hash"]:
            raise ValueError("文档版本已更新，请重新获取目录，不能混用不同版本")
        chunks, incomplete = self.document_chunks(f)
        group = self.sections(chunks).get(section)
        if not group:
            raise ValueError("章节不存在，请先获取文档目录")
        text, spans = "", []
        for c in group:
            part = c["text"]
            # Merge only exact adjacent suffix/prefix overlaps; retain meaningful repetitions otherwise.
            overlap = next((n for n in range(min(len(text), len(part), 2000), 19, -1) if text[-n:] == part[:n]), 0)
            start = max(0, len(text) - overlap)
            text += ("\n" if text and not overlap else "") + part[overlap:]
            spans.append((start, len(text), c))
        page = text[offset:offset + 6000]
        if not page:
            return {"status": "end_of_section", "next_offset": None}
        contributing = [c for start, end, c in spans if start < offset + len(page) and end > offset]
        ev = self.evidence({**contributing[0], "_chunk_ids": [c["id"] for c in contributing]}, f, page, section=section, offset=offset)
        return {"evidence": [ev], "next_offset": offset + len(ev.get("text", "")) if offset + len(ev.get("text", "")) < len(text) else None,
                "total_chars": len(text), "incomplete": incomplete,
                "note": "已入库的解析文本；图片和未解析内容不可见"}

    def validate(self, name, arguments):
        spec = next((t for t in TOOLS if t["name"] == name), None)
        if spec is None:
            raise ValueError("未知工具；仅允许列出的知识库只读工具")
        if isinstance(arguments, str):
            if len(arguments) > 8000:
                raise ValueError("参数过长")
            arguments = json.loads(arguments)
        schema = spec["parameters"]
        if not isinstance(arguments, dict) or set(arguments) - schema["properties"].keys() or set(schema["required"]) - arguments.keys():
            raise ValueError("参数缺失或含有未授权字段，请按工具定义重试")
        for key, value in arguments.items():
            p = schema["properties"][key]
            if p["type"] == "integer":
                if type(value) is not int or not p.get("minimum", 0) <= value <= p.get("maximum", 10000000):
                    raise ValueError(f"{key} 必须是范围内的整数")
            elif not isinstance(value, str) or not p.get("minLength", 0) <= len(value.strip()) <= p.get("maxLength", 1000):
                raise ValueError(f"{key} 文本长度不合法")
        return arguments

    def execute(self, name, arguments):
        self.check()
        try:
            args = self.validate(name, arguments)
            key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
            if key in self.cache:
                self.validate_versions()
                return {**self.cache[key], "cached": True}
            if name == "get_kb_overview":
                result = self.overview()
            elif name == "list_documents":
                query, offset = args.get("query", "").casefold(), args.get("offset", 0)
                files = [f for f in self.files() if query in f["relative_path"].casefold()]
                result = {"documents": [self.brief(f) for f in files[offset:offset + 30]], "total": len(files),
                          "next_offset": offset + 30 if offset + 30 < len(files) else None}
            elif name == "get_document_outline":
                result = self.outline(**args)
            elif name == "read_document":
                result = self.read(**args)
            else:
                result = self.search(**args)
            self.check()
            self.cache[key] = result
            return result
        except (HTTPException, PipelineCancelled):
            raise
        except ValueError as exc:
            return {"error": "invalid_request_or_document", "message": str(exc)}
        except Exception:
            logger.exception("Knowledge tool failed: %s", name)
            return {"error": "unavailable", "message": "知识库工具暂不可用，这不代表没有资料；可尝试其他工具或说明限制"}

    def validate_versions(self):
        files = {f["id"]: f for f in self.files()}
        for h in self.hits:
            f = files.get(h["file_id"])
            if not f or f["status"] != "done" or f["content_hash"] != h["content_hash"] or not set(h["_chunk_ids"]).issubset(self.ids(f)):
                raise ValueError("本次阅读的文档已更新或删除，请重新提问以获取当前版本")

    def sources(self):
        self.validate_versions()
        from src.qa.rag import _hits_to_sources
        return [{**source, "citation_id": i + 1, "content": h["text"], "document_id": h["file_id"],
                 "content_hash": h["content_hash"], "chunk_ids": h["_chunk_ids"], "section_index": h["section_index"],
                 "page": h.get("page"), "offset": h["offset"]}
                for i, (source, h) in enumerate(zip(_hits_to_sources(self.hits), self.hits))]
