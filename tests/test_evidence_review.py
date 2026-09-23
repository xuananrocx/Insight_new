import json

import pytest

from src.qa.evidence_state import Review, answer_payload
from src.qa.conversation_context import estimate


def point(**changes):
    return dict(id='platform', question='Linux 支持什么驱动？', status='supported',
                finding='Linux 仅支持标准驱动', evidence=[{'citation': 1, 'quote': '仅支持标准驱动'}], gap='', **changes)


def hit(text='Linux 仅支持标准驱动。', **changes):
    return dict(text=text, file_id=1, source_name='驱动.md', content_hash='v1', section_index=0,
                section_label='Linux', reading={'section_complete': True}, **changes)


@pytest.mark.parametrize('invalid', [
    {'evidence': [{'citation': 9, 'quote': '仅支持标准驱动'}]},
    {'evidence': [{'citation': True, 'quote': '仅支持标准驱动'}]},
    {'evidence': [{'citation': 1, 'quote': '支持快速驱动'}]},
    {'evidence': []}, {'status': 'conflict'}, {'extra': 'unexpected'}])
def test_bad_review_is_atomic_and_never_overwrites_valid_record(invalid):
    review = Review()
    assert review.update({'points': [point()]}, [hit()])['recorded']
    before = review.snapshot()
    assert review.update({'points': [{**point(), **invalid}]}, [hit()])['error']
    assert review.snapshot() == before


def test_review_preserves_other_points_and_updates_same_id():
    review = Review()
    review.update({'points': [point()]}, [hit()])
    other = {**point(), 'id': 'memory', 'question': '内存占用多少？', 'status': 'unknown',
             'finding': '', 'evidence': [], 'gap': '没有测量数据'}
    assert review.update({'points': [other]}, [hit()])['unresolved'] == ['memory']
    assert len(review.points) == 2
    assert review.update({'points': [{**other, 'gap': '需要测试环境和负载'}]}, [hit()])['recorded']
    assert len(review.points) == 2


def test_packet_prioritizes_referenced_whole_source_and_reports_omission():
    review = Review()
    review.update({'points': [point()]}, [hit()])
    sources = [hit(), hit(text='irrelevant ' * 4000)]
    packet = answer_payload(sources, review, [], 1200, estimate)
    assert [e['citation'] for e in packet['evidence']] == [1]
    assert packet['evidence'][0]['text'] == sources[0]['text']
    assert packet['omitted_citations'] == [2]
    assert estimate(json.dumps(packet, ensure_ascii=False)) <= 1200


def test_packet_budget_error_does_not_silently_drop_history_correction():
    with pytest.raises(ValueError, match='上下文预算'):
        answer_payload([hit()], Review(), [{'text': '纠正：现在是 Linux。' * 500}], 100, estimate)


def test_packet_compacts_exact_duplicates_but_keeps_aliases_and_independent_sources():
    sources = [hit(), hit(), {**hit(), 'file_id': 2}]
    packet = answer_payload(sources, Review(), [], 5000, estimate)
    assert len(packet['evidence']) == 2
    assert packet['evidence'][0]['same_text_citations'][0]['citation'] == 2
    assert packet['evidence'][1]['citation'] == 3
    assert packet['omitted_citations'] == []


def test_packet_omitted_duplicate_group_reports_all_ids():
    sources = [hit('x' * 10000), hit('x' * 10000)]
    packet = answer_payload(sources, Review(), [], 800, estimate)
    assert packet['evidence'] == []
    assert packet['omitted_citations'] == [1, 2]


def test_native_result_compacts_contained_text_without_crossing_versions():
    from src.qa.evidence_state import compact_tool_result
    first = dict(citation=1, document_id=1, version='v1', section=0, text='abcdef',
                 reading=dict(offset_basis='section', start=10, end=16))
    second = {**first, 'citation': 2, 'text': 'cde', 'reading': dict(offset_basis='section', start=12, end=15)}
    delivered = []
    assert compact_tool_result({'evidence':[first]}, delivered)['evidence'][0]['text'] == 'abcdef'
    compact = compact_tool_result({'evidence':[second]}, delivered)['evidence'][0]
    assert 'text' not in compact and compact['text_reference'] == dict(citation=1, start_in_text=2, length=3)
    assert second['text'] == 'cde'
    for change in ({'version':'v2'}, {'document_id':2}, {'section':1}, {'text':'xyz'}):
        assert 'text' in compact_tool_result({'evidence':[{**second, **change}]}, list(delivered))['evidence'][0]
