"""MCP-only bounded canonical scans. Result paging and scan progress are distinct."""
from __future__ import annotations

import copy
import re

from src.knowledge import reading_index
from src.qa import indexed_reading, tool_navigation

SLICE_CHARS = 65536
CALL_CHARS = 524288
MAX_ENTRIES = 160
MAX_MATCHES = 2400


def begin(kb, files, query, mode, bounds=None):
    versions, progress = [], []
    for file in files:
        kb.check()
        info = reading_index.info(file)
        if not info:
            return None
        versions.append({'id': file['id'], 'version': file['content_hash'], 'artifact': info['artifact']})
        progress.append({'id': file['id'], 'section': -1, 'offset': 0, 'done': False, 'started': False})
    return {'versions': versions, 'progress': progress, 'query': query, 'mode': mode,
            'bounds': bounds or {}, 'next_file': 0, 'scanned_chars': 0, 'matches_seen': 0}


def scan(kb, state, limit, offset=0):
    state = copy.deepcopy(state)
    tool_navigation.validate_snapshot(kb, state)
    entries, work, matches = [], 0, 0
    query, mode, bounds = state['query'], state['mode'], state['bounds']
    expression = re.escape(query)
    if mode == 'identifier':
        expression = r'(?<![A-Za-z0-9_])' + expression + r'(?![A-Za-z0-9_])'
    pattern = re.compile(expression, re.I)
    count = len(state['progress'])
    while count and not all(p['done'] for p in state['progress']) and work < CALL_CHARS and len(entries) < MAX_ENTRIES and matches < MAX_MATCHES:
        index = state['next_file'] % count
        state['next_file'] = (index+1) % count
        progress = state['progress'][index]
        if progress['done']:
            continue
        file = kb.file(progress['id'])
        saved = state['versions'][index]
        kb.check()
        with tool_navigation.timed(kb, 'mcp_canonical_scan'):
            with reading_index.open_index(file) as conn:
                # open_index can race a rebuild; never label new coordinates with an old artifact.
                from pathlib import Path
                if Path(conn.execute('PRAGMA database_list').fetchone()[2]).name != saved['artifact']:
                    raise ValueError('阅读索引已更新，请重新搜索')
                clauses, params = ['id>=?'], [max(0, progress['section'])]
                if bounds.get('section') is not None:
                    clauses.append('id=?')
                    params.append(bounds['section'])
                if bounds.get('sheet') is not None:
                    clauses.append("json_extract(extra,'$.sheet')=?")
                    params.append(bounds['sheet'])
                obj = None
                if bounds.get('object_id') is not None:
                    obj = conn.execute('SELECT * FROM objects WHERE id=?', (bounds['object_id'],)).fetchone()
                    if not obj:
                        raise ValueError('对象范围已失效')
                    clauses.append('id=?')
                    params.append(obj['section'])
                row = conn.execute('SELECT id,label,length(text) AS chars FROM sections WHERE '+ ' AND '.join(clauses)+' ORDER BY id LIMIT 1', params).fetchone()
                progress['started'] = True
                if not row:
                    progress['done'] = True
                    continue
                begin_at, end_at = (obj['start'], obj['end']) if obj else (0, row['chars'])
                position = max(begin_at, progress['offset']) if progress['section'] == row['id'] else begin_at
                stop = min(end_at, position+SLICE_CHARS, position+CALL_CHARS-work)
                if position >= end_at:
                    progress.update(section=row['id']+1, offset=0)
                    continue
                # Prefix/suffix preserve identifier and literal matching across slice boundaries.
                load_start = max(begin_at, position-max(250, len(query)+1))
                load_end = min(end_at, stop+max(650, len(query)+1))
                text = conn.execute('SELECT substr(text,?,?) FROM sections WHERE id=?', (load_start+1, load_end-load_start, row['id'])).fetchone()[0]
                windows = []
                next_position = stop
                file_matches = 0
                for match in pattern.finditer(text):
                    start, end = load_start+match.start(), load_start+match.end()
                    if start < position or start >= stop:
                        continue
                    kb.check()
                    if matches >= MAX_MATCHES or file_matches >= 256 or len(windows) >= 24 or len(entries)+len(windows) >= MAX_ENTRIES:
                        next_position = start
                        break
                    lo, hi = max(begin_at, start-250), min(end_at, end+650)
                    if windows and lo <= windows[-1]['end'] and hi-windows[-1]['start'] <= 2400:
                        windows[-1]['end'] = max(hi, windows[-1]['end'])
                        windows[-1]['positions'].append([start, end])
                    else:
                        windows.append({'start': lo, 'end': hi, 'positions': [[start, end]]})
                    matches += 1
                    file_matches += 1
                for window in windows:
                    lo, hi = window['start'], window['end']
                    snippet = conn.execute('SELECT substr(text,?,?) FROM sections WHERE id=?', (lo+1, hi-lo, row['id'])).fetchone()[0]
                    entries.append({'id': f"mcp:{file['id']}:{row['id']}:{lo}", 'file_id': file['id'],
                        'section_index': row['id'], 'section_label': row['label'], 'text': snippet,
                        'reading_artifact': saved['artifact'], '_section_start': lo, '_section_total': row['chars'],
                        '_chunk_ids': [], 'match_positions': window['positions']})
                work += next_position-position
                progress.update(section=row['id'], offset=next_position)
                if next_position >= end_at:
                    progress.update(section=row['id']+1, offset=0)
    state['scanned_chars'] += work
    state['matches_seen'] += matches
    complete = all(p['done'] for p in state['progress'])
    next_scan = None
    if not complete:
        next_scan = 'scan_pending'
        kb.scan_refs[next_scan] = state
    coverage = {'source': 'parsed_original', 'scan_complete': complete, 'candidate_limited': not complete,
        'capacity_limit_reached': not complete, 'parse_completeness': 'unknown',
        'documents_requested': count, 'documents_started': sum(p['started'] for p in state['progress']),
        'documents_completed': sum(p['done'] for p in state['progress']),
        'scanned_chars': state['scanned_chars'], 'call_scanned_chars': work,
        'matches_seen': state['matches_seen'], 'call_matches': matches, 'merged_evidence_count': len(entries),
        'total_matches': state['matches_seen'] if complete else None}
    snapshot = {'kind': 'mcp_canonical', 'entries': entries, 'versions': state['versions'],
                'query': query, 'mode': mode, 'coverage': coverage, 'bounds': bounds, 'next_scan_state': state if next_scan else None}
    key = tool_navigation.save(kb, snapshot)
    return page(kb, key, kb.search_pages[key], offset, limit)


def page(kb, key, snapshot, offset, limit):
    tool_navigation.validate_snapshot(kb, snapshot)
    evidence = []
    for hit in snapshot['entries'][offset:offset+limit]:
        file = kb.file(hit['file_id'])
        if evidence and len(hit['text']) > kb.max_chars-kb.used_chars:
            break
        ev = kb.evidence(hit, file, offset=hit['_section_start'])
        if ev.get('error'):
            break
        ref = indexed_reading.reference(kb, file, hit['reading_artifact'], hit['section_index'], hit['_section_start'])
        ev.update(read_ref=ref, scope_ref=tool_navigation.scope_ref(kb, file, hit['reading_artifact'], section=hit['section_index']),
                  location={'status': 'exact_coordinate', 'section': hit['section_index'], 'start': hit['_section_start']},
                  match_positions=hit['match_positions'])
        evidence.append(ev)
    next_offset = offset+len(evidence)
    more = next_offset < len(snapshot['entries'])
    next_scan = None
    # Do not suggest scan continuation before current result pages have been consumed.
    if not more and snapshot.get('next_scan_state'):
        next_scan = 'scan_pending'
        kb.scan_refs[next_scan] = snapshot['next_scan_state']
    tool_navigation.validate_snapshot(kb, snapshot)
    return {'evidence': evidence, 'status': 'found' if evidence else 'no_match',
            'next_cursor': tool_navigation.cursor(kb, key, next_offset, limit) if more else None,
            'next_scan_cursor': next_scan, 'matching_chunks': len(snapshot['entries']),
            'coverage': snapshot['coverage'],
            'search_scope': {'kind': 'mcp_canonical', 'match_mode': snapshot['mode'], 'selected_range': snapshot['bounds']},
            'scan_query': snapshot['query'], 'scan_mode': snapshot['mode']}
