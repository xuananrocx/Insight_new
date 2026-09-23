"""Bounded reads of original spreadsheet coordinates; no business interpretation."""
import json
from pathlib import Path

from openpyxl.utils.cell import range_boundaries

from src.knowledge import reading_index

MAX_ROWS = 40
MAX_CHARS = 24000
MAX_RESPONSE_CHARS = 40000


def read_rows(kb, file, sheet, row_start, row_end, *, expected_artifact=None):
    """Read inclusive original row coordinates from a current canonical artifact.

    Missing rows/cells remain absent. Pagination is by original row, not by
    nonempty-row ordinal. Oversized rows expose a normal text read reference.
    """
    from src.qa.indexed_reading import reference

    kb.check()
    expected_version = file['content_hash']
    file = kb.file(file['id'])  # authorization and current document version
    changed = {'error': 'document_changed', 'message': '文档或阅读索引已更新，原范围入口版本已失效'}
    if file['content_hash'] != expected_version:
        return changed
    if not isinstance(sheet, str) or not sheet:
        raise ValueError('sheet 必须是目录中的工作表名称')
    if type(row_start) is not int or type(row_end) is not int or row_start < 1 or row_end < row_start:
        raise ValueError('行号从 1 开始，row_end 必须不小于 row_start')
    info = reading_index.info(file)
    if not info:
        return {'error': 'index_unavailable', 'message': '该文档没有可用的原文阅读索引'}
    if expected_artifact is not None and info['artifact'] != expected_artifact:
        return changed
    def still_current():
        current = kb.file(file['id'])
        current_info = reading_index.info(current)
        return (current['content_hash'] == expected_version and current_info is not None
                and current_info['artifact'] == info['artifact'])
    stop = min(row_end, row_start + MAX_ROWS - 1)
    result = {**kb.brief(file), 'sheet': sheet, 'index_artifact': info['artifact'],
              'requested_range': {'start': row_start, 'end': row_end}, 'rows': [],
              'evidence': [], 'merged_ranges': [], 'display_header': None,
              'business_header_identified': False, 'parse_completeness': 'unknown',
              'note': '行列号均为原始坐标；只列出已解析的非空单元格，不填充空值或合并单元格。展示首行不等于业务表头。'}
    used = 0
    found = False
    seen_rows = set()
    merged = {}
    first_row = last_row = None
    blocked = None
    with reading_index.open_index(file) as conn:
        if conn is None:
            return changed
        actual_path = conn.execute('PRAGMA database_list').fetchone()[2]
        if Path(actual_path).resolve() != (reading_index.root() / info['artifact']).resolve():
            return changed
        for section in conn.execute('SELECT * FROM sections ORDER BY id'):
            kb.check()
            meta = json.loads(section['extra'])
            if meta.get('sheet') != sheet:
                continue
            found = True
            cells = meta.get('cell_rows', [])
            if not cells:
                continue
            numbers = [r['row'] for r in cells]
            first_row = min(numbers + ([first_row] if first_row is not None else []))
            last_row = max(numbers + ([last_row] if last_row is not None else []))
            if result['display_header'] is None:
                header = cells[0]
                result['display_header'] = {'row': header['row'], 'status': meta.get('header_status'),
                    'read_ref': reference(kb, file, info['artifact'], section['id'], header['start'], header['end'])}
            for ref in meta.get('merged_ranges', []):
                try:
                    c1, r1, c2, r2 = range_boundaries(ref)
                except ValueError:
                    continue
                if r1 <= stop and r2 >= row_start and ref not in merged:
                    merged[ref] = (c1, r1, c2, r2)
            for cell in cells:
                number = cell['row']
                if blocked is not None or number in seen_rows or not row_start <= number <= stop:
                    continue
                seen_rows.add(number)
                ref = reference(kb, file, info['artifact'], section['id'], cell['start'], cell['end'])
                raw = section['text'][cell['start']:cell['end']]
                cost = len(raw) + len(json.dumps(cell['values'], ensure_ascii=False)) + 600
                if used + cost > MAX_CHARS:
                    blocked = number
                    result['pending_row'] = {'row': number, 'read_ref': ref, 'reason': 'response_size_limit'}
                    continue
                hit = {'id': f"index:{info['artifact']}:{section['id']}", 'text': raw,
                       'reading_artifact': info['artifact'], '_chunk_ids': [],
                       'section_label': section['label'], 'sheet': sheet, 'row_start': number, 'row_end': number}
                ev = kb.evidence(hit, file, raw, section=section['id'], offset=cell['start'], total_chars=len(section['text']))
                if ev.get('error') or len(ev.get('text', '')) < len(raw):
                    blocked = number
                    result['pending_row'] = {'row': number, 'read_ref': ref, 'reason': ev.get('error', 'evidence_budget')}
                    if ev.get('text'):
                        result['evidence'].append(ev)
                    continue
                entry = {'row': number, 'values': cell['values'], 'citation': ev['citation'], 'read_ref': ref}
                if len(json.dumps(result, ensure_ascii=False)) + len(json.dumps([entry, ev], ensure_ascii=False)) > MAX_RESPONSE_CHARS - 8000:
                    blocked = number
                    result['pending_row'] = {'row': number, 'read_ref': ref, 'reason': 'response_size_limit'}
                    continue
                result['rows'].append(entry)
                result['evidence'].append(ev)
                used += cost
        if not found:
            raise ValueError('工作表不存在，请使用目录返回的工作表名称')
        if first_row is None:
            if not still_current():
                return changed
            return {'error': 'row_coordinates_unavailable', 'sheet': sheet,
                    'message': '当前索引没有该工作表的原始行列坐标',
                    'index_artifact': info['artifact']}
        actual_stop = blocked - 1 if blocked is not None else stop
        # Resolve merged anchors from any block of the same sheet, including
        # anchors outside the requested range. Never fill the covered cells.
        pending = {ref: b for ref, b in merged.items() if b[1] <= actual_stop}
        result['merged_ranges_truncated'] = len(pending) > 20
        pending = dict(list(pending.items())[:20])
        anchors = {}
        for section in conn.execute('SELECT * FROM sections ORDER BY id'):
            if not pending:
                break
            kb.check()
            meta = json.loads(section['extra'])
            if meta.get('sheet') != sheet:
                continue
            for cell in meta.get('cell_rows', []):
                for ref, (col, row, _, _) in list(pending.items()):
                    if cell['row'] != row:
                        continue
                    value = cell['values'].get(str(col))
                    entry = {'range': ref, 'anchor': {'row': row, 'column': col,
                        'read_ref': reference(kb, file, info['artifact'], section['id'], cell['start'], cell['end'])}}
                    raw = section['text'][cell['start']:cell['end']]
                    if value is not None and used + len(raw) + len(str(value)) + 600 <= MAX_CHARS:
                        hit = {'id': f"index:{info['artifact']}:{section['id']}", 'text': raw,
                               'reading_artifact': info['artifact'], '_chunk_ids': [],
                               'section_label': section['label'], 'sheet': sheet, 'row_start': row, 'row_end': row}
                        ev = kb.evidence(hit, file, raw, section=section['id'], offset=cell['start'], total_chars=len(section['text']))
                        within_size = (len(json.dumps(result, ensure_ascii=False)) +
                            len(json.dumps(list(anchors.values()), ensure_ascii=False)) +
                            len(json.dumps([value, ev], ensure_ascii=False)) < MAX_RESPONSE_CHARS - 6000)
                        if not ev.get('error') and len(ev.get('text', '')) == len(raw) and within_size:
                            entry['anchor'].update(value=value, citation=ev['citation'])
                            if not any(e['citation'] == ev['citation'] for e in result['evidence']):
                                result['evidence'].append(ev)
                            used += len(raw) + len(str(value)) + 600
                        else:
                            entry['anchor']['value_status'] = 'evidence_budget' if within_size else 'response_size_limit'
                    else:
                        entry['anchor']['value_status'] = 'absent_in_parsed_cells' if value is None else 'response_size_limit'
                    anchors[ref] = entry
                    del pending[ref]
        result['merged_ranges'] = list(anchors.values()) + [
            {'range': ref, 'anchor': {'row': b[1], 'column': b[0], 'value_status': 'not_present_in_index'}}
            for ref, b in pending.items()]
    result['returned_range'] = {'start': row_start, 'end': actual_stop} if actual_stop >= row_start else None
    result['indexed_nonempty_row_bounds'] = {'start': first_row, 'end': last_row}
    result['range_complete'] = blocked is None and stop == row_end
    result['next_range'] = {'start': actual_stop + 1, 'end': row_end} if actual_stop < row_end else None
    result['previous_range'] = {'start': max(1, row_start - MAX_ROWS), 'end': row_start - 1} if row_start > 1 else None
    result['following_range'] = {'start': stop + 1, 'end': min(last_row, stop + MAX_ROWS)} if last_row and last_row > stop else None
    kb.check()
    if not still_current():
        return changed
    return result
