"""Aggregate retained team usage without exposing request/response contents."""
import time
from src.core import accounts as a


def query(*, days=7, provider_id='', model='', search='', include_tests=False,
          sort='calls', page=1, page_size=20, user_key=None):
    where = ["p.scope='team'", 'l.created_at>=?']
    args = [int((time.time() - days * 86400) * 1000)]
    for field, value in [('p.id', provider_id), ('l.model', model)]:
        if value:
            where.append(field + '=?')
            args.append(value)
    if not include_tests:
        where.append("l.scene NOT IN ('test','web_search_test','tools_test')")
    if search:
        where.append("(instr(lower(COALESCE(u.username,'')),lower(?))>0 OR instr(lower(COALESCE(profile.display_name,'')),lower(?))>0)")
        args.extend([search, search])
    if user_key is not None:
        where.append("COALESCE(o.owner_id,'unknown')=?")
        args.append(user_key)
    base = """FROM ai_call_logs l JOIN permission_call_usage c ON c.log_id=l.id
      JOIN auth_providers p ON p.id=c.provider_id
      LEFT JOIN auth_objects o ON o.kind='log' AND o.object_id=CAST(l.id AS TEXT)
      LEFT JOIN auth_users u ON u.id=o.owner_id LEFT JOIN permission_profiles profile ON profile.user_id=u.id WHERE """ + ' AND '.join(where)
    metrics = "COUNT(*) calls,SUM(CASE WHEN l.success=0 THEN 1 ELSE 0 END) failures,MAX(l.created_at) last_call"
    summary = a.rows("SELECT " + metrics + ",COUNT(DISTINCT o.owner_id) active_users " + base, args)[0]
    summary['failures'] = summary['failures'] or 0
    grouping = "COALESCE(o.owner_id,'unknown')"
    fields = "COALESCE(o.owner_id,'unknown') user_key,COALESCE(u.username,'未知用户') username,COALESCE(profile.display_name,'') display_name"
    if user_key is not None:
        grouping = 'p.id,l.model'
        fields = "p.id provider_id,p.name provider_name,COALESCE(l.model,'未知模型') model"
    grouped = 'SELECT ' + fields + ',' + metrics + ' ' + base + ' GROUP BY ' + grouping
    total = a.rows('SELECT COUNT(*) total FROM (' + grouped + ')', args)[0]['total']
    page = min(page, max(1, (total + page_size - 1) // page_size))
    order = {'calls': 'calls DESC', 'failures': 'failures DESC', 'recent': 'last_call DESC'}[sort]
    rows = a.rows(grouped + ' ORDER BY ' + order + ',' + grouping + ' LIMIT ? OFFSET ?', [*args, page_size, (page-1)*page_size])
    return dict(items=rows, total=total, page=page, page_size=page_size, summary=summary)
