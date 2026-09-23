import json
import uuid

import pytest

from src.core import accounts
from src.db import metadata_db as db
from src.knowledge import reading_index as ri
from src.knowledge.parsers.base import ParsedSection
from src.qa.knowledge_tools import KnowledgeTools
from src.qa import table_reading


@pytest.fixture
def table(tmp_path, monkeypatch):
    db.init_db()
    monkeypatch.setattr(accounts, 'enabled', False)
    kid = uuid.uuid4().hex
    db.create_kb(kid, 'tables', kid)
    path = tmp_path / 'table.xlsx'
    path.write_bytes(b'test')
    fid = db.upsert_file(path.name, str(path), ri.fingerprint(path), 4, 'xlsx', kb_id=kid)
    db.set_file_processed(fid, [])
    file = db.get_file_by_path(path.name, kid)
    builder = ri.Builder()
    for sid, numbers in enumerate(([1, 2, 3], [1] + list(range(10, 81)))):
        text = ''
        rows = []
        for number in numbers:
            values = {'1': 'Title' if number == 1 else f'value{number}', '3': 'third'}
            line = f"| {values['1']} |  | third |"
            start = len(text)
            text += line + '\n'
            rows.append({'row': number, 'values': values, 'start': start, 'end': start + len(line)})
        builder.add(ParsedSection(text, path, sid, f'part{sid}', {
            'sheet': 'Quotes', 'cell_rows': rows, 'merged_ranges': ['A2:B20'],
            'header_status': 'display only'}))
    builder.finish()
    with db.get_cursor() as cur:
        builder.publish(cur, fid, file['content_hash'])
    return file, KnowledgeTools(kid)


def test_rows_preserve_coordinates_merge_anchor_and_evidence(table):
    file, kb = table
    result = table_reading.read_rows(kb, file, 'Quotes', 10, 12)
    assert [r['row'] for r in result['rows']] == [10, 11, 12]
    assert result['rows'][0]['values'] == {'1': 'value10', '3': 'third'}
    anchor = result['merged_ranges'][0]['anchor']
    assert (anchor['row'], anchor['column'], anchor['value']) == (2, 1, 'value2')
    assert 'value2' in result['evidence'][-1]['text']
    assert anchor['citation'] == result['evidence'][-1]['citation']
    assert result['display_header']['row'] == 1
    assert not result['business_header_identified']
    assert result['range_complete'] and result['next_range'] is None
    assert result['following_range']['start'] == 13


def test_rows_paginate_original_coordinates_and_deduplicate_repeated_header(table):
    file, kb = table
    first = table_reading.read_rows(kb, file, 'Quotes', 1, 80)
    assert first['returned_range'] == {'start': 1, 'end': 40}
    assert [r['row'] for r in first['rows']].count(1) == 1
    assert first['next_range'] == {'start': 41, 'end': 80}
    second = table_reading.read_rows(kb, file, 'Quotes', **{'row_start': 41, 'row_end': 80})
    assert 0 < len(second['rows']) <= 40
    assert not set(r['row'] for r in first['rows']) & set(r['row'] for r in second['rows'])
    collected = [r['row'] for r in second['rows']]
    if second['next_range']:
        third = table_reading.read_rows(kb, file, 'Quotes', second['next_range']['start'], second['next_range']['end'])
        collected.extend(r['row'] for r in third['rows'])
        assert third['range_complete']
    assert collected == list(range(41, 81))
    assert len(json.dumps(second, ensure_ascii=False)) < 64000


def test_budget_never_exposes_unread_cell_values(table):
    file, kb = table
    kb.max_chars = 3
    result = table_reading.read_rows(kb, file, 'Quotes', 10, 12)
    assert result['rows'] == []
    assert result['returned_range'] is None
    assert result['next_range'] == {'start': 10, 'end': 12}
    assert result['pending_row']['reason'] == 'evidence_budget'
    assert not result['range_complete']


def test_size_limited_row_exposes_read_reference(table, monkeypatch):
    file, kb = table
    monkeypatch.setattr(table_reading, 'MAX_CHARS', 100)
    result = table_reading.read_rows(kb, file, 'Quotes', 10, 12)
    assert result['pending_row']['reason'] == 'response_size_limit'
    assert result['pending_row']['read_ref'] in kb.read_refs
    assert not result['rows']


def test_invalid_range_sheet_and_permission(table):
    file, kb = table
    with pytest.raises(ValueError):
        table_reading.read_rows(kb, file, 'Quotes', 0, 5)
    with pytest.raises(ValueError):
        table_reading.read_rows(kb, file, 'missing', 1, 5)
    def denied():
        raise PermissionError('denied')
    kb.check_cancel = denied
    with pytest.raises(PermissionError):
        table_reading.read_rows(kb, file, 'Quotes', 1, 5)


def test_rejects_stale_caller_version_and_artifact(table):
    file, kb = table
    assert table_reading.read_rows(kb, {**file, 'content_hash': 'old'}, 'Quotes', 1, 2)['error'] == 'document_changed'
    assert table_reading.read_rows(kb, file, 'Quotes', 1, 2, expected_artifact='old.sqlite')['error'] == 'document_changed'


def test_rejects_artifact_changed_during_read(table, monkeypatch):
    file, kb = table
    original = ri.info
    calls = 0
    def replaced(current):
        nonlocal calls
        calls += 1
        result = original(current)
        # initial info + open_index's lookup use the old artifact; final check differs.
        return {**result, 'artifact': 'replacement.sqlite'} if calls >= 3 else result
    monkeypatch.setattr(ri, 'info', replaced)
    assert table_reading.read_rows(kb, file, 'Quotes', 1, 2)['error'] == 'document_changed'


def test_rejects_opened_artifact_mismatch(table, monkeypatch):
    file, kb = table
    original = ri.info
    calls = 0
    def replaced(current):
        nonlocal calls
        calls += 1
        result = original(current)
        return {**result, 'artifact': 'different.sqlite'} if calls == 1 else result
    monkeypatch.setattr(ri, 'info', replaced)
    assert table_reading.read_rows(kb, file, 'Quotes', 1, 2)['error'] == 'document_changed'
