"""Read-only, request-scoped knowledge tools. IDs and versions come from storage."""
from __future__ import annotations

import json
import hashlib
import logging
import re
import time
from src.qa import tool_navigation
from pathlib import PurePosixPath

from fastapi import HTTPException

from src.core import accounts, vector_store
from src.db import metadata_db as db
from src.qa import retrieval, bm25_index, field_search, evidence_state, reading_structure
from src.qa.trace import TraceCollector, PipelineCancelled

logger = logging.getLogger(__name__)


def definition(name, description, properties=None, required=None):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}


LEGACY_TOOLS = [
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


QUERY = {"type": "string", "minLength": 1, "maxLength": 1000, "description": "关键词、字段名、错误码或自然语言问题"}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 8, "description": "最多返回的原文片段数"}
TOOLS = [
    definition("get_kb_overview", "了解当前授权知识库及相关文档目录；目录仅供选择来源，不是原文证据，未列出的文档仍可搜索。"),
    definition("list_documents", "按名称查找文档及真实编号，支持分页。用于寻找文件、类型或名称中的版本；空结果仅表示文件名未命中，不代表正文无资料。",
               {"query": {"type": "string", "maxLength": 200, "description": "文件名关键词；空字符串列出文档"}, "offset": {"type": "integer", "minimum": 0, "description": "使用上次返回的 next_offset"}}),
    definition("search_knowledge", "在整个当前授权知识库搜索原文，无需文档编号。用于未知来源或跨文档查找；结果含匹配类型和 read_ref，可继续阅读。相关结果不等于精确字段命中。",
               {"query": QUERY, "limit": LIMIT, "offset": {"type":"integer","minimum":0,"maximum":10000}, "match_mode": {"type":"string","enum":["related","exact","identifier","keyword","semantic","hybrid"]}}, ["query"]),
    definition("search_document", "在已确定的文档中定位字段、符号、错误码或章节。document_id 来自目录或搜索。exact 模式查完整字面字符串；related 模式查相关内容。返回附近正文、位置及 read_ref，未命中仅适用于本次查找范围。",
               {"query": QUERY, "document_id": {"type": "integer", "minimum": 1, "description": "已返回的真实文档编号"}, "limit": LIMIT,
                "offset": {"type":"integer","minimum":0,"maximum":10000}, "match_mode": {"type": "string", "enum": ["related", "exact", "identifier", "semantic", "hybrid"], "description": "related 为关键词相关；exact 为子串；identifier 为完整标识符；semantic 为语义；hybrid 融合语义与关键词"}}, ["query", "document_id"]),
    definition("get_document_outline", "查看文档章节、对象和成员目录。可按 query 查结构体、类或标题；object_id 来自对象目录，用于分页查看全部成员。对象 read_ref 可直接读完整定义；boundary_known 表示边界是否可靠识别。",
               {"document_id": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0},
                "query": {"type": "string", "maxLength": 200}, "object_id": {"type": "integer", "minimum": 1, "description": "仅查看成员时填写，必须使用 objects 返回的编号；按名称查对象时省略"}}, ["document_id"]),
    definition("read_document", "使用目录或搜索返回的 read_ref 阅读原文及表格上下文。对象目录的引用可读取完整对象；coverage 区分对象边界、已读范围与未读内容。用 next_read_ref 继续当前章节，next_section_ref 阅读相邻章节。仅已入库文本可见；表格无法还原的合并单元格不作推断。",
               {"read_ref": {"type": "string", "minLength": 1, "maxLength": 80, "description": "本轮工具返回的阅读标识，不要自行拼接编号、版本或偏移量"}}, ["read_ref"]),
]
# Legacy shapes remain accepted internally, but are no longer advertised to models.
import copy
COMPAT_TOOLS = copy.deepcopy(TOOLS)
_doc_compat=next(t for t in COMPAT_TOOLS if t['name']=='search_document')
_doc_compat['parameters']['properties'].update({
    'section':{'type':'integer','minimum':0},'object_id':{'type':'integer','minimum':1},
    'sheet':{'type':'string','minLength':1,'maxLength':200}})
_outline=next(t for t in TOOLS if t['name']=='get_document_outline')
_outline['parameters']['properties'].pop('object_id')
_outline['description']='按名称查找文档中的章节和对象，query 可省略；返回真实 read_ref、scope_ref、对象及工作表。成员目录使用 get_object_members。分页使用 next_offset。例：document_id=目录编号，query=结构名；无需猜对象编号。'
MODE={'type':'string','enum':['related','exact','identifier','keyword','semantic','hybrid'],
      'description':'exact 子串，identifier 标识符，keyword/related 关键词，semantic 语义候选，hybrid 混合。'}
REF={'type':'string','minLength':1,'maxLength':80,'description':'本轮目录或搜索返回的 scope_ref；不是 read_ref，也不是名称或编号'}
TOOLS.extend([
 definition('search_scope','搜索 scope_ref 指向的完整章节、对象或工作表。范围由目录提供，不组合章节、工作表和对象编号。exact/identifier 直接搜索已解析原文；语义模式有有限候选，覆盖见 coverage。续页使用 continue_search。',
            {'scope_ref':REF,'query':QUERY,'match_mode':MODE,'limit':LIMIT},['scope_ref','query']),
 definition('get_object_members','列出对象成员。scope_ref 使用目录 objects 返回的对象范围；offset 使用 next_offset。返回成员和原文入口，无需同时提供对象名或编号。',
            {'scope_ref':REF,'offset':{'type':'integer','minimum':0}},['scope_ref']),
 definition('continue_search','继续搜索结果的固定快照，不重新检索或改变排序。cursor 使用 next_cursor；文档/索引变化或快照被回收时返回明确错误。',
            {'cursor':{'type':'string','minLength':1,'maxLength':80}},['cursor']),
 definition('read_table_rows','读取 Excel 原始行区间，scope_ref 使用 sheets 返回的工作表范围。行号来自搜索/阅读坐标，row_start 与 row_end 均包含。返回原始列号和值、合并锚点、实际读取范围与续页范围；展示首行不等于业务表头，不填充空白。',
            {'scope_ref':REF,'row_start':{'type':'integer','minimum':1},'row_end':{'type':'integer','minimum':1}},['scope_ref','row_start','row_end'])
])
for _tool in TOOLS:
    if _tool['name'] in ('search_document','search_knowledge'):
        _tool['description'] += ' scope_ref 表示可选的完整范围；范围内搜索使用 search_scope。续页优先将 next_cursor 传给 continue_search，固定本次结果顺序。location 给出原文候选位置，reading_fallback 表示旧入口原因；分页完成与检索穷尽分别说明。'
    if _tool['name']=='read_document':
        _tool['description'] += ' read_status 区分分页、范围末尾和解析完整性；coverage.object_complete 仅表示对象原文累计读全。previous_read_ref 回读前方原文。表格原始行区间读取使用 read_table_rows。'
LABELS.update(search_document='搜索文档内容',search_scope='搜索指定范围',get_object_members='查看对象成员',continue_search='继续搜索结果',read_table_rows='读取表格行')


class KnowledgeToolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class KnowledgeTools:
    def __init__(self, kb_id, check_cancel=lambda: None, *, max_chars=48000, top_k=8, question=''):
        self.kb_id = kb_id
        self.check_cancel = check_cancel
        self.max_chars = max_chars
        self.top_k = min(8, max(1, top_k or 8))
        self.hits: list[dict] = []
        self.cache: dict[str, dict] = {}
        self.used_chars = 0
        self.question = question
        self.read_refs = {}
        self.scope_refs = {}
        self.search_pages = {}
        self.search_queries = {}
        self.search_cursors = {}
        self.document_cache = {}
        self.timings = {}

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
            raise KnowledgeToolError("document_not_found", "当前知识库中没有此文档，请从目录或搜索结果获取真实编号")
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
        with tool_navigation.timed(self,'chunk_fetch'):
            result = self.collection().get(ids=ids, include=["documents", "metadatas"])
        self.check()
        return {cid: {**(meta or {}), "id": cid, "text": text or ""}
                for cid, text, meta in zip(result["ids"], result["documents"], result["metadatas"])}

    def document_chunks(self, f):
        if f["status"] != "done":
            raise KnowledgeToolError("document_not_ready", "文档尚未成功入库，无法读取；这不代表原文件没有内容")
        ids = self.ids(f)
        if not ids:
            raise ValueError("文档未解析出可读文字，可能需要 OCR 或重新导入")
        # Stored IDs already have file-wide order, also valid for legacy chunks without section metadata.
        cache_key=(f['id'],f['content_hash'],f.get('chunk_ids_json'))
        if cache_key in self.document_cache:
            return self.document_cache[cache_key]
        started=time.monotonic()
        chunks = []
        for start in range(0, min(len(ids), 10000), 500):
            live = self.live_chunks(ids[start:start + 500])
            chunks.extend(live[cid] for cid in ids[start:start + 500] if cid in live and live[cid].get("chunk_type") != "summary")
        if self.file(f["id"])["content_hash"] != f["content_hash"]:
            raise ValueError("文档版本已更新，请重新获取目录")
        if not chunks:
            raise ValueError("文档索引缺失，请重新导入")
        result=(chunks,len(chunks)!=len(ids))
        self.timings['chunk_load']=self.timings.get('chunk_load',0)+round((time.monotonic()-started)*1000,3)
        cost=sum(len(c['text']) for c in chunks)
        if cost<=2000000:
            while self.document_cache and (len(self.document_cache)>=4 or sum(sum(len(c['text']) for c in v[0]) for v in self.document_cache.values())+cost>4000000):
                del self.document_cache[next(iter(self.document_cache))]
            self.document_cache[cache_key]=result
        return result

    def make_ref(self, document_id, version, section=0, offset=0):
        spec = dict(document_id=document_id, version=version, section=section, offset=offset)
        token = 'read_' + hashlib.sha256(json.dumps([self.kb_id, spec], sort_keys=True).encode()).hexdigest()[:32]
        self.read_refs[token] = spec
        return token

    def read_reference(self, read_ref):
        if read_ref not in self.read_refs:
            return {'error': 'invalid_read_ref', 'message': '阅读标识不属于本轮已返回资料，请重新搜索或查看目录'}
        spec = self.read_refs[read_ref]
        if 'artifact' in spec:
            from src.qa import indexed_reading
            return indexed_reading.read(self, **spec)
        return self.read(**spec)

    def catalog(self, files):
        terms = retrieval._terms(self.question) if self.question else set()
        ranked = sorted(files, key=lambda f: (-len(terms & retrieval._terms(f['relative_path'])), f['id']))
        selected, cost = [], 0
        for f in ranked:
            item = self.brief(f)
            item['name'] = item['name'][:300]
            size = len(json.dumps(item, ensure_ascii=False))
            if len(selected) >= 12 or cost + size > 4500:
                break
            selected.append(item)
            cost += size
        return selected

    def overview(self):
        files = self.files()
        kb = db.get_kb(self.kb_id)
        if not kb:
            raise ValueError("知识库不存在")
        summary = db.get_kb_global_summary(self.kb_id)
        current = {str(f["id"]): f["content_hash"] for f in files if f["status"] == "done"}
        fresh = bool(summary and summary.get("snapshot") == current)
        catalog = self.catalog(files)
        return {"document_catalog": catalog, "catalog_truncated": len(catalog) < len(files),
                "catalog_scope": "按问题与文件名相关性排列的候选；可继续查找文档或搜索全库",
                "name": kb["name"], "description": (kb.get("description") or "")[:2000],
                "documents": len(files), "ready": len(current), "not_ready": len(files) - len(current),
                "summary": summary["summary"][:3000] if fresh else None,
                "summary_status": "current_background_only" if fresh else "missing_or_stale",
                "note": "概览提供背景与文档导航，不包含可引用的原文片段。"}

    def evidence(self, hit, f, text=None, *, section=None, offset=0, total_chars=None):
        self.check()
        is_read = text is not None
        original = text if is_read else hit['text']
        text = original if is_read else original[:2500]
        section = section if section is not None else hit.get("section_index", 0)
        section = section if isinstance(section, int) and section >= 0 else 0
        located = is_read or '_section_start' in hit
        if not is_read and located:
            offset = hit['_section_start']
            total_chars = hit['_section_total']
        key = (f["id"], f["content_hash"], hit["id"], section, offset, is_read, text, hit.get("reading_artifact"))
        for i, prior in enumerate(self.hits):
            if prior["_key"] == key or (prior['file_id'] == f['id'] and prior['content_hash'] == f['content_hash']
                    and prior.get('reading_artifact') == hit.get('reading_artifact')
                    and prior['section_index'] == section and prior['text'] == text
                    and (not located or prior['offset'] == offset)
                    and prior.get('reading', {}).get('kind') == ('section' if is_read else 'search_excerpt')):
                return {**self.public_evidence(prior, i + 1), 'reused': True}
        remaining = self.max_chars - self.used_chars
        if remaining <= 0:
            return {"error": "evidence_budget", "message": "本轮原文读取容量已用完；此前返回的资料仍有效"}
        intervals = [(p['reading']['start'], p['reading']['end']) for p in self.hits
                     if located and p['file_id'] == f['id'] and p['content_hash'] == f['content_hash']
                     and p.get('reading_artifact') == hit.get('reading_artifact')
                     and p['section_index'] == section and p.get('reading', {}).get('offset_basis') == 'section']
        if located:
            # Charge only previously unread coordinates; never collapse independent documents.
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if evidence_state.uncovered(offset, offset + middle, intervals) <= remaining:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low]
        else:
            text = text[:remaining]
        end = offset + len(text)
        reading = {'artifact': hit.get('reading_artifact'), 'kind': 'section' if is_read else 'search_excerpt',
                   'offset_basis': 'section' if located else 'chunk',
                   'start': offset, 'end': end,
                   'truncated': len(text) < len(original),
                   'budget_truncated': len(text) < (len(original) if is_read else min(len(original), 2500)),
                   'index_incomplete': bool(hit.get('_index_incomplete', False)),
                   'prefix_omitted': bool(hit.get('_snippet_prefix_omitted', False)),
                   'section_complete': bool(is_read and offset == 0 and end == total_chars and not hit.get('_index_incomplete')),
                   'total_chars': total_chars if located else len(original)}
        read_hint = {'document_id': f['id'], 'version': f['content_hash'], 'section': section,
                     'offset': end if is_read and total_chars is not None and end < total_chars else 0}
        name = PurePosixPath(f["relative_path"].replace("\\", "/")).name
        saved = {**hit, "_key": key, "text": text, "file_id": f["id"], "content_hash": f["content_hash"],
                 "source_path": f["relative_path"], "source_name": name, "title": hit.get("title") or name,
                 "file_type": f.get("file_type", ""), "section_index": section, "offset": offset,
                 "_chunk_ids": hit.get("_chunk_ids", [hit["id"]]),
                 'reading': reading, 'read_hint': read_hint}
        self.hits.append(saved)
        self.used_chars += evidence_state.uncovered(offset, end, intervals) if located else len(text)
        return self.public_evidence(saved, len(self.hits))

    def public_evidence(self, hit, number):
        if hit.get('reading_artifact'):
            from src.qa.indexed_reading import reference
            ref = reference(self, {'id':hit['file_id'],'content_hash':hit['content_hash']}, hit['reading_artifact'], hit['section_index'], hit['offset'])
        else:
            ref = self.make_ref(hit['file_id'], hit['content_hash'], hit['section_index'], max(0,hit['offset']-300) if hit.get('reading',{}).get('offset_basis')=='section' else 0)
        return {"citation": number, "document_id": hit["file_id"], "name": hit["source_name"], "version": hit["content_hash"],
                "section": hit["section_index"], "section_label": hit.get("section_label", ""),
                "page": hit.get("page"), "offset": hit["offset"], "text": hit["text"],
                'reading': hit.get('reading', {}), 'read_hint': hit.get('read_hint'),
                'structure': hit.get('structure'), 'scope_warning': hit.get('scope_warning'),
                'read_ref': hit.get('canonical_read_ref', ref), 'location': hit.get('location')}

    def search(self, query, document_id=None, limit=None, match_mode="related", offset=0, section=None, object_id=None, sheet=None, _snapshot=None, _snapshot_key=None):
        files = self.files()
        selected_scope = None
        scoped = any(v is not None for v in (section, object_id, sheet))
        if scoped and document_id is None:
            raise ValueError('指定范围搜索需要 document_id')
        limit = min(limit or self.top_k, 8)
        query_key=json.dumps([query,document_id,match_mode,section,object_id,sheet],ensure_ascii=False)
        old_key=self.search_queries.get(query_key)
        if _snapshot is None and old_key in self.search_pages:
            token=tool_navigation.cursor(self,old_key,offset,limit)
            _,snap=tool_navigation.get_page(self,token)
            if snap['kind']=='canonical':
                from src.qa import canonical_search
                return canonical_search.page(self,old_key,snap,offset,limit)
            _snapshot=snap;_snapshot_key=old_key
        note = None
        total_matches = None
        more = False
        exact = (lambda text: bool(re.search(r'(?<![A-Za-z0-9_])'+re.escape(query)+r'(?![A-Za-z0-9_])',text,re.I))) if match_mode == 'identifier' else (lambda text: query.casefold() in text.casefold())
        from src.knowledge import reading_index
        from src.qa import canonical_search
        indexed_files=[self.file(document_id)] if document_id is not None else [f for f in files if f['status']=='done']
        if _snapshot is None and (scoped or (match_mode in ('exact','identifier') and indexed_files and all(reading_index.info(f) for f in indexed_files))):
            bounds={k:v for k,v in {'section':section,'object_id':object_id,'sheet':sheet}.items() if v is not None}
            result=canonical_search.search(self,indexed_files,query,match_mode,limit,offset,bounds)
            self.search_queries[query_key]=result.pop('_snapshot_key')
            return result
        incomplete=False
        if _snapshot is not None:
            pool=_snapshot['pool']; total_matches=_snapshot['total_matches']; note=_snapshot['note']
            incomplete=_snapshot['incomplete']
            more=offset+limit<len(pool);hits=pool[offset:offset+limit]
        else:
            if document_id is not None:
                f = self.file(document_id)
                chunks, incomplete = self.document_chunks(f)
                located_chunks = []
                for group in self.sections(chunks).values():
                    section_text, spans = self.join_section(group)
                    located_chunks.extend({**c, '_section_start': start, '_section_total': len(section_text),
                                           '_index_incomplete': incomplete} for start, _, c in spans)
                chunks = located_chunks
                if scoped:
                    from src.qa.indexed_reading import scope_chunks
                    chunks, selected_scope = scope_chunks(self,f,chunks,section,object_id,sheet)
                if match_mode in ('exact','identifier'):
                    pool = [c for c in chunks if exact(c['text'])]
                else:
                    with tool_navigation.timed(self,'keyword_rank'):
                        pool = field_search.rank(query,chunks,len(chunks),retrieval._terms)
                    if match_mode in ('semantic','hybrid'):
                        from src.core import llm_client
                        try:
                            with tool_navigation.timed(self,'embedding'):
                                vectors,_ = llm_client.get_client().embed([query])
                            with tool_navigation.timed(self,'vector_query'):
                                semantic = vector_store.query_by_embedding(vectors[0], k=min(100,max(20,offset+limit+1)),
                                    where={'file_id':document_id},collection_name=db.get_kb(self.kb_id)['collection_name'])
                            owners = {c['id']:c for c in chunks}
                            semantic = [owners[h['id']] for h in semantic if h['id'] in owners]
                            if match_mode == 'semantic':
                                pool = semantic
                            else:
                                scores = {}
                                by_id = {}
                                for group in (pool,semantic):
                                    for rank,h in enumerate(group):
                                        scores[h['id']] = scores.get(h['id'],0)+1/(60+rank+1)
                                        by_id[h['id']] = h
                                pool = [by_id[cid] for cid in sorted(scores,key=scores.get,reverse=True)]
                        except (HTTPException,PipelineCancelled):
                            raise
                        except Exception:
                            note = '语义检索失败，已降级为文档内关键词搜索'
                pool = self.unique_chunks(pool)
                total_matches = len(pool)
                more = offset+limit < len(pool)
                hits = pool[offset:offset+limit]
                note = note or ('文档内搜索；索引不完整' if incomplete else '文档内搜索')
            else:
                trace = TraceCollector()
                trace.cancel_check = self.check
                trace.on_stage_complete(lambda stage:self.timings.__setitem__('retrieval_'+stage['stage'],self.timings.get('retrieval_'+stage['stage'],0)+stage.get('duration_ms',0)))
                if match_mode in ('exact','identifier'):
                    pool = []
                    incomplete = False
                    for file in files:
                        if file['status'] != 'done' or not self.ids(file):
                            continue
                        self.check()
                        doc_chunks, partial = self.document_chunks(file)
                        incomplete = incomplete or partial
                        pool.extend(c for c in doc_chunks if exact(c['text']))
                    total_matches = len(pool)
                    note = '全库字面搜索；索引不完整' if incomplete else '全库字面搜索'
                elif match_mode == 'semantic':
                    from src.core import llm_client
                    with tool_navigation.timed(self,'embedding'):
                        vectors,_ = llm_client.get_client().embed([query])
                    with tool_navigation.timed(self,'vector_query'):
                        pool = vector_store.query_by_embedding(vectors[0],k=min(100,offset+limit+1),collection_name=db.get_kb(self.kb_id)['collection_name'])
                elif match_mode == 'keyword':
                    with tool_navigation.timed(self,'keyword_query'):
                        pool = bm25_index.query_enhanced(query,offset+limit+1,self.kb_id)
                else:
                    try:
                        with tool_navigation.timed(self,'hybrid_retrieval'):
                            pool, _ = retrieval.search(query, 20, self.kb_id, trace, deep=False)
                    except (HTTPException, PipelineCancelled):
                        raise
                    except Exception:
                        self.check()
                        logger.warning('Agent semantic search unavailable; trying lexical index', exc_info=True)
                        with tool_navigation.timed(self,'keyword_query'):
                            pool = bm25_index.query_enhanced(query,offset+limit+1,self.kb_id)
                        note = '语义检索暂不可用，本次仅使用关键词检索'
                pool = self.unique_chunks(pool)
                more = offset+limit < len(pool)
                hits = pool[offset:offset+limit]
        snapshot_truncated=False
        if _snapshot is None:
            kept=[];snapshot_chars=0
            for candidate in pool:
                size=len(json.dumps(candidate,ensure_ascii=False))
                if len(kept)>=1500 or snapshot_chars+size>2500000:
                    snapshot_truncated=True;break
                kept.append(candidate);snapshot_chars+=size
            pool=kept;more=offset+limit<len(pool);hits=pool[offset:offset+limit]
            relevant=[f for f in files if any(h.get('id') in self.ids(f) for h in pool)]
            snapshot={'kind':'legacy','pool':pool,'versions':[{'id':f['id'],'version':f['content_hash']} for f in relevant],
                      'query':query,'document_id':document_id,'mode':match_mode,'note':note,'incomplete':incomplete,'total_matches':total_matches,'capacity_limit_reached':snapshot_truncated}
            _snapshot_key=tool_navigation.save(self,snapshot)
            self.search_queries[query_key]=_snapshot_key
        tool_navigation.validate_snapshot(self,self.search_pages[_snapshot_key])
        # Authoritative membership uses current file records, not possibly stale chunk metadata.
        owners = {cid: f for f in files if f["status"] == "done" for cid in self.ids(f)}
        ids = [h["id"] for h in hits if h.get("id") in owners]
        live = self.live_chunks(ids)
        result = []
        section_cache = {}
        for h in hits:
            cid = h.get("id")
            if cid not in live or live[cid].get("chunk_type") == "summary":
                continue
            f = owners[cid]
            if self.file(f["id"])["content_hash"] != f["content_hash"]:
                continue
            candidate = {**h, **live[cid]}
            if scoped:
                candidate.update(h)  # Canonical clipped range must survive authoritative metadata refresh.
            if '_section_start' not in candidate:
                if f['id'] not in section_cache:
                    document, incomplete_index = self.document_chunks(f)
                    section_cache[f['id']] = (self.sections(document), incomplete_index)
                groups, incomplete_index = section_cache[f['id']]
                group = groups.get(candidate.get('section_index', 0), [])
                whole, spans = self.join_section(group)
                location = next((start for start, _, c in spans if c['id'] == cid), None)
                if location is not None:
                    candidate.update(_section_start=location, _section_total=len(whole), _index_incomplete=incomplete_index)
            fields = field_search.identifiers(query)
            needles = [query] if match_mode in ('exact','identifier') else [query, *fields]
            positions = [candidate['text'].casefold().find(v.casefold()) for v in needles]
            positions = [v for v in positions if v >= 0]
            snippet_start = max(0, min(positions)-300) if positions else 0
            source_text = candidate['text']
            candidate['text'] = source_text[snippet_start:]
            if '_section_start' in candidate:
                candidate['_section_start'] += snippet_start
            candidate['structure'] = reading_structure.describe(source_text, snippet_start, min(len(source_text), snippet_start+2500))
            candidate['structure']['line_basis'] = 'chunk'
            candidate['structure']['chunk_id'] = cid
            candidate['_snippet_prefix_omitted'] = snippet_start > 0
            ev = self.evidence(candidate, f, offset=candidate.get('_section_start', snippet_start))
            ev['match_type'] = 'exact_literal' if query.casefold() in ev.get('text', '').casefold() else 'identifier' if field_search.matches(ev.get('text', ''), fields) else 'related'
            ev['match_scope'] = 'returned_excerpt'
            ev['matched_fields'] = field_search.matches(ev.get('text', ''), field_search.identifiers(query))
            warning = field_search.scope_warning(self.question or query, live[cid])
            if warning:
                ev['scope_warning'] = warning
            if 'citation' in ev:
                self.hits[ev['citation']-1].update({k: ev[k] for k in ('match_type', 'scope_warning') if k in ev})
            if 'citation' in ev:
                from src.qa import indexed_reading
                with tool_navigation.timed(self,'source_location'):
                    location = indexed_reading.locate(self,f,ev['text'])
                ev['location'] = location
                saved = self.hits[ev['citation']-1]
                saved['location'] = location
                if location['status']=='exact':
                    ev['read_ref']=location['candidates'][0]['read_ref']
                    info=reading_index.info(f)
                    ev['scope_ref']=tool_navigation.scope_ref(self,f,info['artifact'],section=location['candidates'][0]['section'])
                    saved['canonical_read_ref']=ev['read_ref']
                else:
                    # Keep a truthful legacy fallback; ambiguous canonical candidates stay explicit.
                    ev['reading_fallback'] = location['status']
                logger.info('知识库搜索定位 document=%s status=%s candidates=%s',f['id'],location['status'],len(location['candidates']))
            result.append(ev)
        fields = field_search.identifiers(query)
        matched = set(word for ev in result for word in ev.get('matched_fields', []))
        missing = [word for word in fields if word not in matched]
        logger.info('字段检索 kb=%s document=%s fields=%s matched=%s missing=%s hits=%s scope_warnings=%s',
                    self.kb_id, document_id, fields, sorted(matched), missing, len(result), sum(bool(ev.get('scope_warning')) for ev in result))
        navigation = []
        from src.qa import indexed_reading
        for fid in dict.fromkeys(ev.get('document_id') for ev in result if ev.get('document_id')):
            file = next(f for f in files if f['id']==fid)
            for term in field_search.identifiers(query)[:4]:
                directory = indexed_reading.outline(self,file,query=term)
                if directory:
                    navigation.extend({'document_id':fid,**obj} for obj in directory.get('objects',[])[:4])
        tool_navigation.validate_snapshot(self,self.search_pages[_snapshot_key])
        return {"objects": navigation[:12],
                "next_cursor":tool_navigation.cursor(self,_snapshot_key,offset+limit,limit) if more else None,
                "coverage":{"candidate_pool_size":len(pool),"exhaustive":False,"source":"legacy_retrieval","capacity_limit_reached":self.search_pages[_snapshot_key].get("capacity_limit_reached",False)},
                "next_offset": offset+limit if more else None, "matching_chunks": total_matches,
                "evidence": result, "note": note, "status": "found" if any('citation' in ev for ev in result) else "no_match",
                'question_context': evidence_state.reading_guidance(self.question or query),
                'reading_note': 'read_ref 提供原文入口；location 描述新索引精确位置或多个候选。reading_fallback 表示保留旧分段入口的原因。offset 坐标仍以 reading.offset_basis 和 artifact 为准。',
                'matched_fields': sorted(matched), 'missing_fields': missing,
                'search_scope': {'kind': 'document' if document_id is not None else 'knowledge_base', 'selected_range': selected_scope, 'document_id': document_id, 'match_mode': match_mode, 'exact_matching_chunks': total_matches if match_mode in ('exact','identifier') else None, 'index_incomplete': incomplete if document_id is not None or match_mode in ('exact','identifier') else None},
                'coverage_note': 'missing_fields 仅描述返回节选，不代表全部原文' }

    @staticmethod
    def unique_chunks(chunks):
        seen=set()
        result=[]
        for chunk in chunks:
            # Only identical source coordinates/IDs are duplicates, never similar meanings.
            key=(chunk.get('file_id'),chunk.get('section_index'),chunk.get('reading_artifact'),
                 chunk.get('_section_start'),chunk.get('text') if '_section_start' in chunk else chunk.get('id'))
            if key not in seen:
                seen.add(key)
                result.append(chunk)
        return result

    @staticmethod
    def sections(chunks):
        groups = {}
        for c in chunks:
            section = c.get("section_index", 0)
            if not isinstance(section, int) or section < 0:
                section = 0
            groups.setdefault(section, []).append(c)
        return groups

    def outline(self, document_id, offset=0, query='', object_id=None):
        f = self.file(document_id)
        from src.qa import indexed_reading
        indexed = indexed_reading.outline(self,f,offset,query,object_id)
        if indexed is not None:
            return tool_navigation.attach(self,indexed)
        chunks, incomplete = self.document_chunks(f)
        sections = [{"section": index, "label": group[0].get("section_label", ""), "chunks": len(group), "read_ref": self.make_ref(f["id"], f["content_hash"], index)}
                    for index, group in self.sections(chunks).items()]
        return {**self.brief(f), "sections": sections[offset:offset + 60],
                "next_offset": offset + 60 if offset + 60 < len(sections) else None,
                "incomplete": incomplete, "legacy_order": any("section_index" not in c for c in chunks)}

    def read(self, document_id, version, section=0, offset=0):
        f = self.file(document_id)
        if version != f["content_hash"]:
            raise KnowledgeToolError("document_changed", "文档版本已更新，请重新获取目录，不能混用不同版本")
        chunks, incomplete = self.document_chunks(f)
        group = self.sections(chunks).get(section)
        if not group:
            raise KnowledgeToolError("section_not_found", "章节不存在，请先获取文档目录")
        text, spans = self.join_section(group)
        end_hint = min(len(text), offset + 6000)
        if end_hint < len(text):
            # Prefer a nearby paragraph boundary, otherwise a complete table/code
            # line. Keep exact coordinates and let continuation read every byte.
            minimum = offset + 4800
            paragraph = text.rfind('\n\n', minimum, end_hint)
            line = text.rfind('\n', minimum, end_hint)
            if paragraph >= minimum:
                end_hint = paragraph + 2
            elif line >= minimum:
                end_hint = line + 1
        page = text[offset:end_hint]
        if not page:
            return {"status": "end_of_section", "next_offset": None}
        contributing = [c for start, end, c in spans if start < offset + len(page) and end > offset]
        ev = self.evidence({**contributing[0], "_chunk_ids": [c["id"] for c in contributing],
                            '_index_incomplete': incomplete}, f, page, section=section, offset=offset, total_chars=len(text))
        if ev.get('error'):
            return ev
        end = offset + len(ev['text'])
        structure = reading_structure.describe(text, offset, end)
        self.hits[ev['citation']-1]['structure'] = structure
        ev['structure'] = structure
        warning = field_search.scope_warning(self.question, contributing[0])
        if warning:
            ev['scope_warning'] = warning
            self.hits[ev['citation']-1]['scope_warning'] = warning
        table = structure.get('table')
        header = table['header'] + '\n' + table['separator'] if table else None
        indices = list(self.sections(chunks))
        next_index = indices[indices.index(section)+1] if indices.index(section)+1 < len(indices) else None
        return {"evidence": [ev], "next_offset": end if end < len(text) else None,
                "total_chars": len(text), "incomplete": incomplete,
                "read_status": {"page_truncated": end<len(text), "range_complete": end==len(text), "index_incomplete": incomplete, "parse_completeness": "unknown", "path": "legacy_chunks"},
                'next_read_ref': self.make_ref(document_id, version, section, end) if end < len(text) else None,
                'next_section_ref': self.make_ref(document_id, version, next_index) if next_index is not None else None,
                'context_header': header,
                'question_context': evidence_state.reading_guidance(self.question),
                'reading_note': '页内截断可能切开表格或代码，请按 next_offset 续读。structure 提供解析行号及可识别的对应表头，不推定未保留的合并单元格。',
                "note": "已入库的解析文本；图片和未解析内容不可见"}

    @staticmethod
    def join_section(group):
        text, spans = '', []
        for c in group:
            part = c["text"]
            # Merge only exact adjacent suffix/prefix overlaps; retain meaningful repetitions otherwise.
            overlap = next((n for n in range(min(len(text), len(part), 2000), 19, -1) if text[-n:] == part[:n]), 0)
            start = max(0, len(text) - overlap) + (1 if text and not overlap else 0)
            text += ("\n" if text and not overlap else "") + part[overlap:]
            spans.append((start, len(text), c))
        return text, spans

    @staticmethod
    def table_header(text):
        lines = text.splitlines()
        for i, line in enumerate(lines[:80]):
            if i and re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*', line):
                return '\n'.join(lines[i-1:i+1])[:2000]
        return None

    def validate(self, name, arguments):
        if isinstance(arguments, str):
            if len(arguments) > 8000:
                raise ValueError('参数过长')
            arguments = json.loads(arguments)
        specs = LEGACY_TOOLS if isinstance(arguments, dict) and ((name == 'read_document' and 'read_ref' not in arguments) or (name == 'search_knowledge' and 'document_id' in arguments)) else TOOLS
        if isinstance(arguments,dict) and ((name=='get_document_outline' and 'object_id' in arguments) or (name=='search_document' and any(k in arguments for k in ('section','object_id','sheet')))):
            specs=COMPAT_TOOLS
        spec = next((t for t in specs if t['name'] == name), None)
        if spec is None:
            raise ValueError("未知工具；仅允许列出的知识库只读工具")
        schema = spec["parameters"]
        if not isinstance(arguments, dict) or set(arguments) - schema["properties"].keys() or set(schema["required"]) - arguments.keys():
            raise ValueError("参数缺失或含有未授权字段，请按工具定义重试")
        for key, value in arguments.items():
            p = schema["properties"][key]
            if "enum" in p and value not in p["enum"]:
                raise ValueError(f"{key} 选项不合法")
            if p["type"] == "integer":
                if type(value) is not int or not p.get("minimum", 0) <= value <= p.get("maximum", 10000000):
                    raise ValueError(f"{key} 必须是范围内的整数")
            elif not isinstance(value, str) or not p.get("minLength", 0) <= len(value.strip()) <= p.get("maxLength", 1000):
                raise ValueError(f"{key} 文本长度不合法")
        return arguments

    def execute(self, name, arguments):
        self.check()
        self.timings={}
        started=time.monotonic()
        try:
            args = self.validate(name, arguments)
            key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
            if key in self.cache and name not in ('search_scope','get_object_members','continue_search','read_table_rows','get_document_outline') and not (name=='read_document' and 'read_ref' in args):
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
                result = self.read_reference(**args) if "read_ref" in args else self.read(**args)
            elif name=='search_scope':
                file,bounds=tool_navigation.resolve(self,args['scope_ref'])
                from src.qa import canonical_search
                result=canonical_search.search(self,[file],args['query'],args.get('match_mode','related'),args.get('limit',self.top_k),
                    bounds={k:v for k,v in bounds.items() if k in ('section','object_id','sheet')})
                result.pop('_snapshot_key',None)
            elif name=='get_object_members':
                file,bounds=tool_navigation.resolve(self,args['scope_ref'])
                if bounds.get('object_id') is None:raise ValueError('该 scope_ref 不是对象范围')
                result=self.outline(file['id'],args.get('offset',0),object_id=bounds['object_id'])
            elif name=='read_table_rows':
                file,bounds=tool_navigation.resolve(self,args['scope_ref'])
                if not bounds.get('sheet'):raise ValueError('该 scope_ref 不是工作表范围；sheets 列表提供工作表入口')
                from src.qa.table_reading import read_rows
                result=read_rows(self,file,bounds['sheet'],args['row_start'],args['row_end'],expected_artifact=bounds['artifact'])
            elif name=='continue_search':
                spec,snap=tool_navigation.get_page(self,args['cursor'])
                if snap['kind']=='canonical':
                    from src.qa import canonical_search
                    result=canonical_search.page(self,spec['key'],snap,spec['offset'],spec['limit'])
                else:
                    result=self.search(snap['query'],snap['document_id'],spec['limit'],snap['mode'],spec['offset'],_snapshot=snap,_snapshot_key=spec['key'])
            else:
                result = self.search(**args)
            self.check()
            with tool_navigation.timed(self,'result_serialization'):
                result_size=len(json.dumps(result,ensure_ascii=False))
            result['tool_metrics']={'phases_ms':dict(self.timings),'total_ms':round((time.monotonic()-started)*1000,3),'result_chars':result_size}
            logger.info('知识库工具阶段 tool=%s kb=%s phases_ms=%s result_chars=%s',name,self.kb_id,self.timings,result_size)
            self.cache[key] = result
            return result
        except (HTTPException, PipelineCancelled):
            raise
        except KnowledgeToolError as exc:
            return {"error": exc.code, "message": str(exc)}
        except ValueError as exc:
            return {"error": "invalid_request_or_document", "message": str(exc)}
        except Exception:
            logger.exception("Knowledge tool failed: %s", name)
            return {"error": "unavailable", "message": "知识库工具暂不可用；本次没有得到有效查询结果"}

    def validate_versions(self):
        files = {f["id"]: f for f in self.files()}
        for h in self.hits:
            f = files.get(h["file_id"])
            if not f or f["status"] != "done" or f["content_hash"] != h["content_hash"] or not set(h["_chunk_ids"]).issubset(self.ids(f)):
                raise KnowledgeToolError("document_changed", "本次阅读的文档已更新或删除，请重新提问以获取当前版本")

        from src.knowledge import reading_index
        checked=set()
        for ref in self.read_refs.values():
            if not ref.get('artifact') or (ref['document_id'],ref['artifact']) in checked:continue
            checked.add((ref['document_id'],ref['artifact']))
            f=files.get(ref['document_id'])
            info=reading_index.info(f) if f else None
            if not info or info['artifact']!=ref['artifact'] or f['content_hash']!=ref['version']:
                raise KnowledgeToolError('document_changed','阅读入口对应的文档或索引已更新')

    def sources(self):
        self.validate_versions()
        from src.qa.rag import _hits_to_sources
        return [{**source, "citation_id": i + 1, "content": h["text"], "document_id": h["file_id"],
                 "content_hash": h["content_hash"], "chunk_ids": h["_chunk_ids"], "section_index": h["section_index"],
                 "page": h.get("page"), "offset": h["offset"], 'reading': h.get('reading', {})}
                for i, (source, h) in enumerate(zip(_hits_to_sources(self.hits), self.hits))]
