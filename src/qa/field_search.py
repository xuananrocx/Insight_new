"""Rank field definitions above generic keyword overlap; keep scope visible."""
import math
import re
from collections import Counter


def identifiers(query):
    return list(dict.fromkeys(word for word in re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}', query)
                             if '_' in word or re.search(r'[a-z][A-Z]|[A-Z]{2,}[a-z]', word)))


def matches(text, fields):
    return [word for word in fields if re.search(r'(?<![A-Za-z0-9_])' + re.escape(word) + r'(?![A-Za-z0-9_])', text, re.I)]


def scope_warning(query, chunk):
    # Only explicit conflicting section labels trigger a warning. Unlabelled
    # material stays eligible; this is not an inferred document permission.
    label = str(chunk.get('section_label') or '')
    groups = [(subject, '银行间', '外汇') for subject in ('沪深', '沪市', '深市', '股票', '个股')]
    if any(subject in query and any(other in label for other in others) for subject, *others in groups):
        return f'章节范围为「{label}」，与问题指定范围可能不同，不能直接据此确认字段含义或映射。'
    return ''


def rank(query, chunks, limit, tokenize):
    fields = identifiers(query)
    terms = tokenize(query)
    words = [tokenize(c['text']) for c in chunks]
    frequency = Counter(word for group in words for word in group)
    ranked = []
    for chunk, group in zip(chunks, words):
        exact = matches(chunk['text'], fields)
        overlap = terms & group
        if not exact and not overlap:
            continue
        score = sum(math.log(1 + len(chunks) / frequency[word]) for word in overlap)
        ranked.append((not bool(scope_warning(query, chunk)), len(exact), score, chunk))
    ranked.sort(key=lambda row: row[:3], reverse=True)
    seen, sections, selected, covered = set(), Counter(), [], set()
    for _, _, _, chunk in ranked:
        fingerprint = re.sub(r'[\s|]+', '', chunk['text']).casefold()
        index = chunk.get('section_index')
        section = index if isinstance(index, int) and index >= 0 else chunk.get('section_label') or chunk['id']
        found = set(matches(chunk['text'], fields))
        if fingerprint in seen or (sections[section] >= 2 and not found - covered):
            continue
        seen.add(fingerprint)
        sections[section] += 1
        covered.update(found)
        selected.append(chunk)
        if len(selected) >= limit:
            break
    return selected
