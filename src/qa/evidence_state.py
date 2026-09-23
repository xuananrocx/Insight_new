"""Objective request-local evidence bookkeeping; never a semantic truth score."""
from __future__ import annotations

import hashlib
import json


def uncovered(start, end, intervals):
    cursor, count = start, 0
    for left, right in sorted(intervals):
        if right <= cursor or left >= end:
            continue
        count += max(0, min(left, end) - cursor)
        cursor = max(cursor, min(right, end))
    return count + max(0, end - cursor)


class Progress:
    def __init__(self):
        self.seen = set()
        self.ranges = {}

    def observe(self, result):
        counts = {'new_evidence': 0, 'new_navigation': 0, 'new_history': 0, 'duplicates': 0}
        for key, kind in [('evidence', 'new_evidence'), ('documents', 'new_navigation'),
                          ('sections', 'new_navigation'), ('objects', 'new_navigation'), ('members', 'new_navigation'), ('turns', 'new_history')]:
            values = result.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict) or value.get('error'):
                    continue
                reading = value.get('reading', {})
                if key == 'evidence' and reading.get('offset_basis') == 'section':
                    coordinate = (value.get('document_id'), value.get('version'), value.get('section'))
                    intervals = self.ranges.setdefault(coordinate, [])
                    start, end = reading['start'], reading['end']
                    counts['new_evidence' if uncovered(start, end, intervals) else 'duplicates'] += 1
                    intervals.append((start, end))
                    continue
                # Presentation fields and citation numbers do not establish novelty.
                item = {k: v for k, v in value.items() if k not in (
                    'citation', 'matched_fields', 'scope_warning', 'reused', 'read_hint', 'question_context')}
                fingerprint = hashlib.sha256(json.dumps([key, item], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if fingerprint in self.seen:
                    counts['duplicates'] += 1
                else:
                    self.seen.add(fingerprint)
                    counts[kind] += 1
        if isinstance(result.get('text'), str) and result['text']:
            fingerprint = hashlib.sha256(result['text'].encode()).hexdigest()
            if fingerprint in self.seen:
                counts['duplicates'] += 1
            else:
                self.seen.add(fingerprint)
                counts['new_history'] += 1
        return counts


def reading_guidance(question):
    return {'original_question': question}



def manifest(hits):
    """Compact inventory with no new facts, stable citations and exact read limits."""
    return [{'citation': i + 1, 'document_id': h['file_id'], 'name': h['source_name'],
             'version': h['content_hash'], 'section': h['section_index'],
             'section_label': h.get('section_label', ''), 'reading': h.get('reading', {}), 'structure': h.get('structure'), 'scope_warning': h.get('scope_warning'), 'match_type': h.get('match_type')}
            for i, h in enumerate(hits)]


REVIEW_TOOL = {
    'name': 'record_evidence_review',
    'description': '记录问题要点、原文依据和未解决项。按需使用，不要求完成记录才能作答。只记录简短结论和原文，不记录思维过程。这不是独立事实核验。',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['points'], 'properties': {
        'points': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'question', 'status', 'finding', 'evidence', 'gap'],
            'properties': {
                'id': {'type': 'string', 'maxLength': 40},
                'question': {'type': 'string', 'maxLength': 300},
                'status': {'type': 'string', 'enum': ['supported', 'partial', 'conflict', 'unknown']},
                'finding': {'type': 'string', 'maxLength': 600},
                'gap': {'type': 'string', 'maxLength': 300},
                'evidence': {'type': 'array', 'maxItems': 6, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['citation', 'quote'],
                    'properties': {'citation': {'type': 'integer', 'minimum': 1},
                                   'quote': {'type': 'string', 'minLength': 1, 'maxLength': 600}}}}
            }}}
    }}
}


class Review:
    def __init__(self):
        self.points = {}

    def update(self, arguments, hits):
        """Atomic, bounded updates. Validate literal provenance, not the model's conclusion."""
        try:
            if isinstance(arguments, str):
                if len(arguments) > 24000:
                    raise ValueError('记录过长')
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict) or set(arguments) != {'points'}:
                raise ValueError('仅允许 points')
            points = arguments['points']
            if not isinstance(points, list) or not 1 <= len(points) <= 8:
                raise ValueError('要点数量应为 1 至 8')
            updated = dict(self.points)
            seen = set()
            for point in points:
                if not isinstance(point, dict) or set(point) != {'id', 'question', 'status', 'finding', 'evidence', 'gap'}:
                    raise ValueError('要点字段不完整或含未知字段')
                for key, maximum in [('id', 40), ('question', 300), ('finding', 600), ('gap', 300)]:
                    if not isinstance(point[key], str) or len(point[key]) > maximum:
                        raise ValueError('要点文本超限')
                if not point['id'].strip() or not point['question'].strip() or point['id'] in seen:
                    raise ValueError('要点编号和问题必须有效且不重复')
                seen.add(point['id'])
                if point['status'] not in ('supported', 'partial', 'conflict', 'unknown'):
                    raise ValueError('要点状态无效')
                refs = point['evidence']
                if not isinstance(refs, list) or len(refs) > 6:
                    raise ValueError('每项最多 6 条依据')
                for ref in refs:
                    if not isinstance(ref, dict) or set(ref) != {'citation', 'quote'}:
                        raise ValueError('依据需要 citation 和 quote')
                    cid, quote = ref['citation'], ref['quote']
                    if type(cid) is not int or not 1 <= cid <= len(hits):
                        raise ValueError('依据引用必须来自本次已读原文')
                    if not isinstance(quote, str) or not quote.strip() or len(quote) > 600 or quote not in hits[cid-1]['text']:
                        raise ValueError('quote 必须逐字来自对应引用的已读原文')
                if point['status'] == 'supported' and not refs:
                    raise ValueError('可答要点必须提供原文依据')
                if point['status'] == 'conflict' and len({r['citation'] for r in refs}) < 2:
                    raise ValueError('冲突要点至少需要两条不同引用')
                updated[point['id']] = json.loads(json.dumps(point))
            if len(updated) > 8:
                raise ValueError('累计最多 8 个要点，请更新已有编号')
            self.points = updated
            return {'recorded': True, **self.snapshot()}
        except (ValueError, TypeError, KeyError) as exc:
            return {'error': 'invalid_review', 'message': str(exc)}

    def snapshot(self):
        return {'points': list(self.points.values()),
                'unresolved': [p['id'] for p in self.points.values() if p['status'] != 'supported'],
                'validation': '仅核对引用和逐字摘录；结论及适用条件仍须对照原文，不能视为独立核验通过'}


def answer_payload(hits, review, history_reads, token_budget, estimate):
    """Keep whole evidence entries and stable IDs; disclose omissions, never silently clip facts."""
    payload = {'review': review.snapshot(), 'history_reads': history_reads,
               'evidence': [], 'omitted_citations': []}
    referenced = {r['citation'] for p in review.points.values() for r in p['evidence']}
    inventory = manifest(hits)
    groups = {}
    for i, h in enumerate(hits):
        key = (h['file_id'], h['content_hash'], h['section_index'], h.get('reading_artifact'), h['text'])
        groups.setdefault(key, []).append(i)
    # Reserve metadata space for omission IDs so the final object stays within budget.
    reserve = estimate(json.dumps(list(range(1, len(hits)+1)))) + 64
    for indices in sorted(groups.values(), key=lambda group: (not any(i+1 in referenced for i in group), group[0])):
        i = indices[0]
        entry = {**inventory[i], 'text': hits[i]['text'],
                 'same_text_citations': [inventory[j] for j in indices[1:]]}
        payload['evidence'].append(entry)
        if estimate(json.dumps(payload, ensure_ascii=False)) + reserve > token_budget:
            payload['evidence'].pop()
            payload['omitted_citations'].extend(j+1 for j in indices)
    if estimate(json.dumps(payload, ensure_ascii=False)) > token_budget:
        raise ValueError('问题记录和历史超出作答上下文预算，请缩短问题或调整模型上下文配置')
    return payload


def compact_tool_result(result, delivered):
    """Reference only verbatim text already delivered in this native exchange."""
    if not result.get('evidence'):
        return result
    entries = []
    for item in result['evidence']:
        current = dict(item)
        text = item.get('text')
        if not isinstance(text, str) or not text:
            entries.append(current)
            continue
        for prior in delivered:
            if any(prior.get(k) != item.get(k) for k in ('document_id', 'version', 'section')):
                continue
            left, right = prior.get('reading', {}), item.get('reading', {})
            if left.get('artifact') != right.get('artifact') or left.get('offset_basis') != 'section' or right.get('offset_basis') != 'section':
                continue
            start = right['start'] - left['start']
            if start >= 0 and prior['text'][start:start + len(text)] == text:
                current.pop('text')
                current['text_reference'] = {'citation': prior['citation'], 'start_in_text': start, 'length': len(text)}
                current['text_note'] = '正文已在此前工具结果中提供；此处保留来源和坐标，不重复发送。'
                break
        else:
            delivered.append(dict(item))
        entries.append(current)
    return {**result, 'evidence': entries}
