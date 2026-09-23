"""Source-coordinate helpers; never infer missing table cells or symbol semantics."""
import re

SEPARATOR = re.compile(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*')

def describe(text, start, end):
    lines = text.splitlines(keepends=True)
    positions, cursor = [], 0
    for line in lines:
        positions.append(cursor)
        cursor += len(line)
    first = max((i for i, pos in enumerate(positions) if pos <= start), default=0)
    last = max((i for i, pos in enumerate(positions) if pos < end), default=first)
    result = {'line_start': first + 1, 'line_end': last + 1, 'line_basis': 'section'}
    for i in range(first, -1, -1):
        line = lines[i].strip()
        if re.match(r'^(#{1,6}\s|\d+(?:\.\d+)+[.\s])', line):
            result['heading'] = line[:300]
            break
    # Report an enclosing declaration only when braces make its scope explicit.
    depth = 0
    for i in range(first, -1, -1):
        line = lines[i]
        depth += line.count('}') - line.count('{')
        if depth < 0 and re.search(r'\b(?:struct|class|enum)\s+\w+', line):
            result['declaration_hint'] = {'line': i+1, 'text': line.strip()[:300]}
            break
        if depth < -1:
            break
    tables = []
    for separator in range(1, len(lines)):
        if not SEPARATOR.fullmatch(lines[separator].strip()) or '|' not in lines[separator-1]:
            continue
        tail = separator + 1
        while tail < len(lines) and lines[tail].strip() and '|' in lines[tail] and not SEPARATOR.fullmatch(lines[tail].strip()):
            tail += 1
        if separator-1 > last or tail <= first:
            continue
        header = lines[separator-1].rstrip('\r\n')
        columns = [v.strip() for v in re.split(r'(?<!\\)\|', header.strip().strip('|'))]
        rows, budget = [], 800
        for i in range(max(first, separator+1), min(last+1, tail)):
            if positions[i] < start or positions[i] + len(lines[i]) > end:
                continue
            cells = [v.strip() for v in re.split(r'(?<!\\)\|', lines[i].strip().strip('|'))]
            if len(rows) >= 3 or sum(len(v) for v in cells) > budget:
                break
            budget -= sum(len(v) for v in cells)
            rows.append({'line': i+1, 'cells': cells, 'column_count_matches': len(cells) == len(columns)})
        tables.append({'header': header[:1200], 'separator': lines[separator].strip()[:1200],
                       'header_start': positions[separator-1], 'column_count': len(columns),
                       'rows': rows, 'rows_complete': len(rows) == max(0, min(last+1,tail)-max(first,separator+1)),
                       'header_truncated': len(header)>1200,
                       'representation': 'parsed_markdown',
                       'limitation': '未推定原文未保留的合并单元格、多级表头或空白值含义'})
        if len(tables) == 2:
            result['table_details_limited'] = True
            break
    if tables:
        result['tables'] = tables
        if len(tables) == 1:
            result['table'] = tables[0]
    return result
