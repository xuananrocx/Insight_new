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
    """Compatibility shim: source labels are data, not business applicability rules."""
    return ''


def rank(query, chunks, limit, tokenize):
    if limit <= 0:
        return []
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
        ranked.append((len(exact), score, chunk))
    ranked.sort(key=lambda row: row[:2], reverse=True)
    seen, selected = set(), []
    for _, _, chunk in ranked:
        origin = (chunk.get('file_id'), chunk.get('content_hash'),
                  chunk.get('reading_artifact'), chunk.get('section_index'))
        if isinstance(chunk.get('_section_start'), int):
            fingerprint = (*origin, 'section', chunk['_section_start'], chunk['text'])
        elif chunk.get('id') is not None:
            fingerprint = (*origin, 'chunk', chunk['id'])
        else:
            # Missing provenance is not evidence that two excerpts are duplicates.
            fingerprint = None
        if fingerprint is not None and fingerprint in seen:
            continue
        if fingerprint is not None:
            seen.add(fingerprint)
        selected.append(chunk)
        if len(selected) >= limit:
            break
    return selected
