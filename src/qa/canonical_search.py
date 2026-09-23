"""Search parsed original text directly; lexical coverage and vector coverage are explicit."""
import json
import re
from src.knowledge import reading_index
from src.qa import indexed_reading, field_search, retrieval
from src.qa.tool_navigation import timed,save,cursor,scope_ref,validate_snapshot

MAX_RESULTS=1500
MAX_CHARS=1800000


def ranges(kb,file,bounds,artifact):
    with reading_index.open_index(file) as conn:
        from pathlib import Path
        if Path(conn.execute('PRAGMA database_list').fetchone()[2]).name!=artifact:
            raise ValueError('读取索引已更新，原搜索范围失效')
        obj=None
        if bounds.get('object_id') is not None:
            obj=conn.execute('SELECT * FROM objects WHERE id=?',(bounds['object_id'],)).fetchone()
            if not obj:raise ValueError('对象编号不存在')
        selected=False
        for row in conn.execute('SELECT * FROM sections ORDER BY id'):
            kb.check()
            if bounds.get('section') is not None and row['id']!=bounds['section']:continue
            meta=json.loads(row['extra'])
            if bounds.get('sheet') is not None and meta.get('sheet')!=bounds['sheet']:continue
            if obj and row['id']!=obj['section']:continue
            selected=True
            yield row,meta,(obj['start'] if obj else 0),(obj['end'] if obj else len(row['text']))
        if not selected and bounds:raise ValueError('指定范围不存在或没有交集')


def search(kb,files,query,mode,limit,offset=0,bounds=None):
    bounds=bounds or {}
    entries=[];versions=[];truncated=False;size=0;semantic_coverage=[];scanned_chars=0
    pattern=re.compile((r'(?<![A-Za-z0-9_])'+re.escape(query)+r'(?![A-Za-z0-9_])') if mode=='identifier' else re.escape(query),re.I)
    terms=retrieval._terms(query)
    for file in files:
        kb.check()
        info=reading_index.info(file)
        if not info:raise ValueError('该文档阅读索引不可用')
        versions.append({'id':file['id'],'version':file['content_hash'],'artifact':info['artifact']})
        vector_hits=None
        if mode in ('semantic','hybrid'):
            vector_hits={}
            try:
                from src.core import llm_client,vector_store
                from src.db import metadata_db
                with timed(kb,'embedding'):
                    vectors,_=llm_client.get_client().embed([query])
                # Progressive candidate expansion is bounded and reported; no claim of exhaustiveness.
                tried=[]
                for count in (20,80,200):
                    with timed(kb,'vector_query'):
                        hits=vector_store.query_by_embedding(vectors[0],k=count,where={'file_id':file['id']},
                            collection_name=metadata_db.get_kb(kb.kb_id)['collection_name'])
                    tried.append(count)
                    for rank,h in enumerate(hits):
                        if h.get('id') in kb.ids(file) and h.get('chunk_type')!='summary':
                            vector_hits.setdefault(h['id'],(rank,h))
                    if len(hits)<count or not bounds:break
                semantic_coverage.append({'document_id':file['id'],'requested_candidates':tried,
                    'returned_candidates':len(vector_hits),'exhaustive':False})
            except Exception as exc:
                from fastapi import HTTPException
                from src.qa.trace import PipelineCancelled
                if isinstance(exc,(HTTPException,PipelineCancelled)):raise
                semantic_coverage.append({'document_id':file['id'],'fallback':'keyword','reason':type(exc).__name__,'exhaustive':False})
                vector_hits=None
        with timed(kb,'canonical_scan'):
            for row,meta,begin,end in ranges(kb,file,bounds,info['artifact']):
                scanned_chars+=end-begin
                if scanned_chars>50000000:
                    truncated=True;break
                text=row['text']; windows=[]; semantic_positions={}
                if mode in ('exact','identifier'):
                    # Match before forming snippets, so boundaries between chunks cannot hide matches.
                    for match in pattern.finditer(text,begin,end):
                        kb.check()
                        windows.append((max(begin,match.start()-250),min(end,match.end()+650),match.start()))
                        if len(windows)>MAX_RESULTS:break
                else:
                    for start in range(begin,end,1000):
                        kb.check()
                        stop=min(end,start+1200)
                        snippet=text[start:stop]
                        if terms & retrieval._terms(snippet) or field_search.matches(snippet,field_search.identifiers(query)):
                            windows.append((start,stop,start))
                        if len(windows)>MAX_RESULTS:break
                    if vector_hits is not None:
                        semantic_windows=[]
                        for rank,h in vector_hits.values():
                            pos=text.find(h['text']) if h.get('text') else -1
                            while pos>=0:
                                kb.check()
                                lo,hi=max(begin,pos),min(end,pos+len(h['text']))
                                if lo<hi:
                                    semantic_windows.append((lo,min(hi,lo+1200),lo))
                                    semantic_positions[(lo,min(hi,lo+1200),lo)]=rank
                                if len(semantic_windows)>MAX_RESULTS:break
                                pos=text.find(h['text'],pos+1)
                            if len(semantic_windows)>MAX_RESULTS:break
                        if mode=='semantic':windows=semantic_windows
                        else:windows+=semantic_windows
                seen=set()
                for lo,hi,match_start in windows:
                    key=(lo,hi,match_start)
                    if key in seen:continue
                    seen.add(key)
                    snippet=text[lo:hi]
                    if len(entries)>=MAX_RESULTS or size+len(snippet)>MAX_CHARS:
                        truncated=True;break
                    entries.append({'id':f"canonical:{file['id']}:{row['id']}:{lo}:{match_start}",
                        'file_id':file['id'],'section_index':row['id'],'section_label':row['label'],
                        '_semantic_rank':semantic_positions.get(key),'text':snippet,'reading_artifact':info['artifact'],'_section_start':lo,
                        '_section_total':len(text),'match_start':match_start,'sheet':meta.get('sheet'),
                        '_chunk_ids':[]})
                    size+=len(snippet)
                if truncated:break
        if truncated:break
    if mode not in ('exact','identifier','semantic'):
        with timed(kb,'keyword_rank'):
            lexical=field_search.rank(query,entries,len(entries),retrieval._terms)
            if mode=='hybrid':
                scores={h['id']:1/(61+i) for i,h in enumerate(lexical)}
                for h in entries:
                    if h['_semantic_rank'] is not None:
                        scores[h['id']]=scores.get(h['id'],0)+1/(61+h['_semantic_rank'])
                entries=sorted([h for h in entries if h['id'] in scores],key=lambda h:scores[h['id']],reverse=True)
            else:entries=lexical
    elif mode=='semantic':
        entries.sort(key=lambda h:h['_semantic_rank'] if h['_semantic_rank'] is not None else 1000000)
    snap={'kind':'canonical','entries':entries,'versions':versions,'query':query,'mode':mode,
          'coverage':{'source':'parsed_original','range':bounds,'scan_complete':not truncated,
            'capacity_limit_reached':truncated,'scanned_chars':min(scanned_chars,50000000),'semantic':semantic_coverage,
            'parse_completeness':'unknown','documents_scanned':len(versions),'documents_requested':len(files)}}
    key=save(kb,snap)
    return {**page(kb,key,snap,offset,limit),'_snapshot_key':key}


def page(kb,key,snap,offset,limit):
    validate_snapshot(kb,snap)
    evidence=[]
    for hit in snap['entries'][offset:offset+limit]:
        file=kb.file(hit['file_id'])
        ev=kb.evidence(hit,file,offset=hit['_section_start'])
        if ev.get('error'):
            return {**ev,'next_cursor':cursor(kb,key,offset+len(evidence),limit),'evidence':evidence}
        with timed(kb,'source_location'):
            location=indexed_reading.locate(kb,file,ev['text'])
        # Coordinates here are canonical: select exactly this occurrence, never a different duplicate.
        options=[p for p in location['candidates'] if p['section']==hit['section_index'] and p['start']==hit['_section_start']]
        ref=options[0]['read_ref'] if options else indexed_reading.reference(kb,file,hit['reading_artifact'],hit['section_index'],hit['_section_start'])
        ev.update(read_ref=ref,location={'status':'exact_coordinate','candidates':options},
                  scope_ref=scope_ref(kb,file,hit['reading_artifact'],section=hit['section_index']),
                  match_start=hit['match_start'])
        kb.hits[ev['citation']-1].update(canonical_read_ref=ref,location=ev['location'])
        evidence.append(ev)
    next_offset=offset+limit if offset+limit<len(snap['entries']) else None
    validate_snapshot(kb,snap)
    return {'evidence':evidence,'status':'found' if evidence else 'no_match','matching_chunks':len(snap['entries']),
            'next_offset':next_offset,'next_cursor':cursor(kb,key,next_offset,limit) if next_offset is not None else None,
            'search_scope':{'kind':'canonical','match_mode':snap['mode'],'selected_range':snap['coverage']['range']},
            'coverage':snap['coverage'],'note':'结果属于已解析原文；分页沿用固定结果快照，覆盖范围见 coverage'}
