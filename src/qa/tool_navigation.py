"""Request-local scope handles and stable, version-bound search pages."""
import hashlib
import json
import time
from contextlib import contextmanager
from src.knowledge import reading_index


@contextmanager
def timed(kb, phase):
    started=time.monotonic()
    try:
        yield
    finally:
        kb.timings[phase]=kb.timings.get(phase,0)+round((time.monotonic()-started)*1000,3)


def scope_ref(kb,file,artifact,**bounds):
    spec={'document_id':file['id'],'version':file['content_hash'],'artifact':artifact,**bounds}
    token='scope_'+hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:32]
    kb.scope_refs[token]=spec
    return token


def resolve(kb,token):
    spec=kb.scope_refs.get(token)
    if not spec:
        raise ValueError('scope_ref 不属于本轮目录或搜索结果')
    file=kb.file(spec['document_id'])
    info=reading_index.info(file)
    if file['content_hash']!=spec['version'] or not info or info['artifact']!=spec['artifact']:
        raise ValueError('范围对应的文档或索引已更新，原 scope_ref 已失效')
    return file,spec


def attach(kb,result):
    """Attach only capabilities derived from actual indexed coordinates."""
    fid=result.get('document_id')
    if not fid or result.get('index_status')!='ready':
        return result
    file=kb.file(fid);info=reading_index.info(file)
    if not info:return result
    result['scope_ref']=scope_ref(kb,file,info['artifact'])
    sheets={}
    for section in result.get('sections',[]):
        section['scope_ref']=scope_ref(kb,file,info['artifact'],section=section['section'])
        if section.get('sheet'):
            sheets[section['sheet']]=scope_ref(kb,file,info['artifact'],sheet=section['sheet'])
    result['sheets']=[{'name':name,'scope_ref':ref} for name,ref in sheets.items()]
    for obj in result.get('objects',[]):
        obj['scope_ref']=scope_ref(kb,file,info['artifact'],object_id=obj['object_id'])
    return result


def save(kb,snapshot):
    # Bounded per-request pages; no cross-user cache or persistent state.
    encoded=json.dumps(snapshot,ensure_ascii=False)
    size=len(encoded)
    if size>4000000:
        raise ValueError('本次候选快照超过 400 万字符容量；可指定更小范围')
    while kb.search_pages and (len(kb.search_pages)>=12 or sum(v['_size'] for v in kb.search_pages.values())+size>8000000):
        del kb.search_pages[next(iter(kb.search_pages))]
    key='search_'+hashlib.sha256(encoded.encode()).hexdigest()[:32]
    kb.search_pages[key]={**snapshot,'_size':size}
    return key


def cursor(kb,key,offset,limit):
    spec={'key':key,'offset':offset,'limit':limit}
    token='page_'+hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:32]
    kb.search_cursors[token]=spec
    if len(kb.search_cursors)>1000:
        del kb.search_cursors[next(iter(kb.search_cursors))]
    return token


def get_page(kb,token):
    kb.check()
    spec=kb.search_cursors.get(token)
    if not spec or spec['key'] not in kb.search_pages:
        raise ValueError('搜索游标不存在或本轮快照已被容量回收')
    snap=kb.search_pages[spec['key']]
    validate_snapshot(kb,snap)
    return spec,snap


def validate_snapshot(kb,snap):
    kb.check()
    for saved in snap['versions']:
        file=kb.file(saved['id'])
        if file['content_hash']!=saved['version'] or file['status']!='done':
            raise ValueError('分页对应文档已更新或删除，搜索快照失效')
        if saved.get('artifact'):
            info=reading_index.info(file)
            if not info or info['artifact']!=saved['artifact']:
                raise ValueError('分页对应阅读索引已更新，搜索快照失效')
