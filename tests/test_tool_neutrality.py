"""Tools retain available source locations without business-specific decisions."""
from src.qa.evidence_state import Progress
from src.qa.field_search import rank, scope_warning


def tokens(text):
    return set(text.split())


def test_ranking_retains_all_matches_in_one_section():
    chunks = [{'id': str(i), 'file_id': 1, 'section_index': 0,
               'text': f'field row{i}'} for i in range(7)]
    assert rank('field', chunks, 20, tokens) == chunks


def test_ranking_keeps_distinct_locations_and_table_layouts():
    chunks = [{'id': str(i), 'file_id': 1, 'section_index': 0, 'text': text}
              for i, text in enumerate(['field | a | b', 'field | ab', 'field | a | b'])]
    assert len(rank('field', chunks + [dict(chunks[0])], 20, tokens)) == 3


def test_ranking_deduplicates_only_same_version_and_coordinate():
    chunk = {'id': 'a', 'file_id': 1, 'content_hash': 'v1', 'section_index': 0,
             '_section_start': 0, 'text': 'field value'}
    chunks = [chunk, {**chunk, 'id': 'b'}, {**chunk, 'content_hash': 'v2'},
              {**chunk, '_section_start': 100}, {**chunk, 'file_id': 2}]
    assert len(rank('field', chunks, 20, tokens)) == 4


def test_ranking_has_no_market_specific_preference():
    chunks = [{'id': 'a', 'text': '沪深 field', 'section_label': '银行间'},
              {'id': 'b', 'text': '沪深 field', 'section_label': '沪深'}]
    assert all(scope_warning('沪深 field', c) == '' for c in chunks)
    assert rank('沪深 field', chunks, 20, tokens) == chunks


def test_progress_separates_original_and_legacy_coordinates():
    progress = Progress()
    def evidence(artifact, start=0, end=10):
        return {'evidence': [{'document_id': 1, 'version': 'v1', 'section': 0,
                             'reading': {'offset_basis': 'section', 'artifact': artifact,
                                         'start': start, 'end': end}}]}
    assert progress.observe(evidence(None))['new_evidence'] == 1
    assert progress.observe(evidence('index-v1'))['new_evidence'] == 1
    assert progress.observe(evidence('index-v1'))['duplicates'] == 1
    assert progress.observe(evidence('index-v1', 5, 20))['new_evidence'] == 1
    assert progress.observe(evidence('index-v2'))['new_evidence'] == 1
