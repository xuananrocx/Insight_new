"""Navigation and reading over immutable canonical document artifacts."""
import json
from src.knowledge import reading_index
from src.qa import reading_structure


def reference(kb, file, artifact, section, start=0, end=None, object_id=None):
    import hashlib
    spec = dict(document_id=file['id'],version=file['content_hash'],artifact=artifact,
                section=section,offset=start,end=end,object_id=object_id)
    key = 'read_' + hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:32]
    kb.read_refs[key] = spec
    return key


def outline(kb,file,offset=0,query='',object_id=None):
    info = reading_index.info(file)
    if not info:
        return None
    with reading_index.open_index(file) as conn:
        if object_id is not None:
            obj = conn.execute('SELECT * FROM objects WHERE id=?',(object_id,)).fetchone()
            if not obj:
                raise ValueError('对象不存在，请使用目录提供的编号')
            if query and query.casefold() not in obj['name'].casefold():
                raise ValueError('对象编号与名称不匹配；按名称查找时请省略 object_id，再使用返回的真实编号')
            members = json.loads(obj['members'])
            return {**kb.brief(file),'object':obj['name'],'members':members[offset:offset+50],
                    'next_offset':offset+50 if offset+50<len(members) else None,
                    'object_boundary_known':bool(obj['complete']),
                    'read_ref':reference(kb,file,info['artifact'],obj['section'],obj['start'],obj['end'],obj['id']),
                    'note':'成员目录用于定位，完整声明和注释请阅读对象原文；不推定字段语义'}
        rows = conn.execute('SELECT id,label,extra,length(text) AS chars FROM sections ORDER BY id LIMIT 51 OFFSET ?',(offset,)).fetchall()
        objects = conn.execute('SELECT * FROM objects WHERE name LIKE ? ORDER BY id LIMIT 51 OFFSET ?',('%'+query+'%',offset)).fetchall()
        result = {**kb.brief(file),'index_status':'ready','index_version':reading_index.VERSION,
                  'sections':[dict(section=r['id'],label=r['label'],sheet=json.loads(r['extra']).get('sheet'),chars=r['chars'],read_ref=reference(kb,file,info['artifact'],r['id'])) for r in rows[:50]],
                  'objects':[],'next_offset':offset+50 if len(objects)>50 or len(rows)>50 else None,
                  'note':'对象目录和成员名称不是完整正文；可按对象读取或查看成员目录。标题和表格边界可能只覆盖解析分段。'}
        for obj in objects[:50]:
            result['objects'].append(dict(object_id=obj['id'],kind=obj['kind'],name=obj['name'],section=obj['section'],
                chars=obj['end']-obj['start'],boundary_known=bool(obj['complete']),member_count=len(json.loads(obj['members'])),
                read_ref=reference(kb,file,info['artifact'],obj['section'],obj['start'],obj['end'],obj['id'])))
        return result


def read(kb,document_id,version,artifact,section,offset=0,end=None,object_id=None):
    file = kb.file(document_id)
    info = reading_index.info(file)
    if file['content_hash']!=version or not info or info['artifact']!=artifact:
        return {'error':'document_changed','message':'文档或阅读索引版本已更新，请重新查看目录'}
    with reading_index.open_index(file) as conn:
        row = conn.execute('SELECT * FROM sections WHERE id=?',(section,)).fetchone()
        if not row:
            raise ValueError('阅读章节不存在')
        text = row['text']
        end = len(text) if end is None else min(end,len(text))
        obj = conn.execute('SELECT * FROM objects WHERE id=?',(object_id,)).fetchone() if object_id else None
        # Small/medium complete objects fit in a single read; large ones remain navigable.
        stop = min(end,offset+12000)
        if stop<end:
            line=text.rfind('\n',offset+9000,stop)
            if line>=0:
                stop=line+1
        if offset>=stop:
            return {'status':'end_of_object' if obj else 'end_of_section'}
        meta = json.loads(row['extra'])
        hit = dict(id=f'index:{artifact}:{section}',text=text[offset:stop],section_label=row['label'],
                   reading_artifact=artifact,_chunk_ids=[],**{k:v for k,v in meta.items() if k in ('page','sheet','row_start','row_end')})
        ev=kb.evidence(hit,file,text[offset:stop],section=section,offset=offset,total_chars=len(text))
        if ev.get('error'):
            return ev
        actual_end=offset+len(ev['text'])
        structure=reading_structure.describe(text,offset,actual_end)
        # The canonical object owns its declaration; nearby brace heuristics do not.
        structure.pop('declaration_hint', None)
        if obj:
            structure['object'] = {'id': obj['id'], 'name': obj['name'], 'kind': obj['kind'],
                                   'start': obj['start'], 'end': obj['end']}
            structure['declaration_hint'] = {'line': text.count('\n',0,obj['start'])+1,
                                             'text': text[obj['start']:obj['end']].splitlines()[0][:300]}

        intervals=[(h['reading']['start'],h['reading']['end']) for h in kb.hits
                   if h.get('reading_artifact')==artifact and h['section_index']==section]
        from src.qa.evidence_state import uncovered
        completeness=dict(object_name=obj['name'] if obj else None,boundary_known=bool(obj['complete']) if obj else False,
                          object_complete=bool(obj and obj['complete'] and uncovered(obj['start'],obj['end'],intervals)==0),
                          returned_to_end=actual_end==end,remaining_chars=end-actual_end)
        if meta.get('cell_rows'):
            cells=[]
            used=0
            for row_meta in meta['cell_rows']:
                if row_meta['start'] < offset or row_meta['end'] > actual_end:
                    continue
                cost=len(json.dumps(row_meta,ensure_ascii=False))
                if used+cost>8000 or len(cells)>=12:
                    break
                cells.append(row_meta)
                used+=cost
            structure['spreadsheet']={'sheet':meta.get('sheet'),'rows':cells,'columns':meta.get('columns'),
                'merged_ranges':meta.get('merged_ranges',[])[:100], 'header_status':meta.get('header_status'),
                'display_header':meta['cell_rows'][0] if meta['cell_rows'] else None,
                'business_header_identified':False,
                'rows_truncated':len(cells)<sum(r['start']>=offset and r['end']<=actual_end for r in meta['cell_rows']),
                'note':'values 的键为原始列号，缺少的单元格为空，不据此推定不支持或默认值；结构列表有容量限制，正文仍以已读范围为准'}
        saved=kb.hits[ev['citation']-1]
        saved['structure']=structure
        saved['reading'].update(completeness)
        ev.update(structure=structure,reading=saved['reading'],read_ref=reference(kb,file,artifact,section,offset,end,object_id))
        following=conn.execute('SELECT id FROM sections WHERE id>? ORDER BY id LIMIT 1',(section,)).fetchone()
        return {'evidence':[ev],'coverage':completeness,
                'read_status': {'page_truncated': actual_end<end, 'range_complete': actual_end==end, 'parse_completeness': 'unknown', 'path': 'canonical_index'},
                'next_read_ref':reference(kb,file,artifact,section,actual_end,end,object_id) if actual_end<end else None,
                'previous_read_ref':reference(kb,file,artifact,section,max(obj['start'] if obj else 0,offset-12000),offset,object_id) if offset>(obj['start'] if obj else 0) else None,
                'next_section_ref':reference(kb,file,artifact,following['id']) if following else None,
                'note':'完整性仅描述原文读取范围，不代表字段语义或模型结论已核实'}


def locate(kb, file, excerpt):
    """Exact source lookup, bounded candidates. Never reuse legacy offsets."""
    info = reading_index.info(file)
    if not info or not excerpt:
        return {'status': 'index_unavailable' if not info else 'empty_excerpt', 'candidates': []}
    candidates = []
    with reading_index.open_index(file) as conn:
        for row in conn.execute('SELECT * FROM sections WHERE instr(text,?)>0 ORDER BY id', (excerpt,)):
            kb.check()
            start = row['text'].find(excerpt)
            while start >= 0:
                stop = start + len(excerpt)
                obj = conn.execute('SELECT * FROM objects WHERE section=? AND start<=? AND end>=? ORDER BY end-start LIMIT 1',
                                   (row['id'], start, stop)).fetchone()
                meta = json.loads(row['extra'])
                begin, end = (obj['start'], obj['end']) if obj and obj['complete'] else (max(0,start-300),None)
                # For tables, start at a whole original row, not in the middle of Markdown.
                cell = next((r for r in meta.get('cell_rows',[]) if r['start']<=start<r['end']),None)
                if cell:
                    begin = cell['start']
                candidates.append({'section':row['id'],'label':row['label'],'sheet':meta.get('sheet'),
                    'start':start,'end':stop,'row':cell.get('row') if cell else None,
                    'object': {'id':obj['id'],'name':obj['name'],'kind':obj['kind']} if obj else None,
                    'read_ref':reference(kb,file,info['artifact'],row['id'],begin,end,obj['id'] if obj and obj['complete'] else None)})
                if len(candidates)>=9:
                    return {'status':'ambiguous','candidates':candidates[:8],'candidates_truncated':True}
                start = row['text'].find(excerpt,start+1)
    return {'status':'exact' if len(candidates)==1 else 'ambiguous' if candidates else 'not_located',
            'candidates':candidates,'candidates_truncated':False,
            'directory_hint':{'document_id':file['id'],'tool':'get_document_outline'} if not candidates else None}


def scope_chunks(kb, file, chunks, section=None, object_id=None, sheet=None):
    """Filter before ranking. Return only text in an explicitly selected canonical range."""
    info=reading_index.info(file)
    if not info:
        raise ValueError('范围搜索需要阅读索引；可省略范围搜索整篇文档，或补建索引')
    ranges=[]
    with reading_index.open_index(file) as conn:
        obj=conn.execute('SELECT * FROM objects WHERE id=?',(object_id,)).fetchone() if object_id is not None else None
        if object_id is not None and not obj:
            raise ValueError('对象编号不存在，请使用目录返回的编号')
        for row in conn.execute('SELECT * FROM sections ORDER BY id'):
            meta=json.loads(row['extra'])
            if section is not None and row['id']!=section or sheet is not None and meta.get('sheet')!=sheet:
                continue
            if obj and row['id']!=obj['section']:
                continue
            ranges.append((row['id'],row['text'], obj['start'] if obj else 0,obj['end'] if obj else len(row['text'])))
    if not ranges:
        raise ValueError('指定范围不存在或范围参数没有交集，请查看文档目录')
    selected=[]
    unresolved=0
    for chunk in chunks:
        kb.check()
        found=False
        for sid,text,begin,end in ranges:
            start=text.find(chunk['text'])
            while start>=0:
                lo,hi=max(start,begin),min(start+len(chunk['text']),end)
                if lo<hi:
                    selected.append({**chunk,'text':text[lo:hi],'section_index':sid,
                        '_section_start':lo,'_section_total':len(text),'reading_artifact':info['artifact']})
                    found=True
                    break
                start=text.find(chunk['text'],start+1)
            if found: break
        if not found: unresolved+=1
    return selected, {'section':section,'object_id':object_id,'sheet':sheet,
                      'index_artifact':info['artifact'],'matched_chunks':len(selected),
                      'note':'仅搜索能精确定位到指定范围的现有检索片段；未定位片段不进入该范围'}
