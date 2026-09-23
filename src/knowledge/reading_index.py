"""Versioned, per-document reading artifacts. No embeddings or model calls.

An artifact is built privately, then its pointer is published in metadata.db.
Readers always validate document hash and artifact version. Failed builds never
replace an existing pointer. SQLite keeps large spreadsheets off the Python heap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.db import metadata_db as db

VERSION = 1
logger = logging.getLogger(__name__)


def schema(cur):
    if "reading_artifact" not in {r[1] for r in cur.execute("PRAGMA table_info(ingest_runs)")}:
        cur.execute("ALTER TABLE ingest_runs ADD COLUMN reading_artifact TEXT")
    cur.execute('''CREATE TABLE IF NOT EXISTS document_read_indexes (
        file_id INTEGER PRIMARY KEY REFERENCES knowledge_files(id) ON DELETE CASCADE,
        content_hash TEXT NOT NULL, format_version INTEGER NOT NULL,
        artifact TEXT NOT NULL, sections INTEGER NOT NULL, objects INTEGER NOT NULL,
        chars INTEGER NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS index_tasks (
        id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES kbs(id) ON DELETE CASCADE,
        state TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
        files_json TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
        results_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_index_task_active ON index_tasks(kb_id) WHERE state IN ('queued','running','cancelling')")


def root():
    path = db.settings.get_path('metadata_db').parent / 'document-indexes'
    path.mkdir(parents=True, exist_ok=True)
    return path


def fingerprint(path, check=lambda: None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1024 * 1024):
            check()
            digest.update(data)
    return digest.hexdigest()


class Builder:
    def __init__(self):
        self.name = uuid.uuid4().hex + '.sqlite'
        self.path = root() / self.name
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript('''
            CREATE TABLE sections (id INTEGER PRIMARY KEY, label TEXT NOT NULL,
                text TEXT NOT NULL, extra TEXT NOT NULL);
            CREATE TABLE objects (id INTEGER PRIMARY KEY, section INTEGER NOT NULL,
                kind TEXT NOT NULL, name TEXT NOT NULL, start INTEGER NOT NULL,
                end INTEGER NOT NULL, complete INTEGER NOT NULL, members TEXT NOT NULL);
            CREATE INDEX idx_objects_section ON objects(section,start);
        ''')
        self.sections = self.objects = self.chars = 0
        self.closed = False

    def add(self, section):
        text = section.text
        self.conn.execute('INSERT INTO sections VALUES (?,?,?,?)',
                          (section.section_index, section.section_label, text, json.dumps(section.extra, ensure_ascii=False)))
        self.sections += 1
        self.chars += len(text)
        for kind, name, start, end, complete, members in structures(text):
            self.conn.execute('INSERT INTO objects(section,kind,name,start,end,complete,members) VALUES (?,?,?,?,?,?,?)',
                              (section.section_index, kind, name, start, end, int(complete), json.dumps(members, ensure_ascii=False)))
            self.objects += 1
        self.conn.commit()

    def finish(self):
        if not self.closed:
            self.conn.close()
            self.closed = True

    def publish(self, cur, file_id, content_hash):
        self.finish()
        cur.execute('''INSERT INTO document_read_indexes(file_id,content_hash,format_version,artifact,sections,objects,chars)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET
            content_hash=excluded.content_hash,format_version=excluded.format_version,artifact=excluded.artifact,
            sections=excluded.sections,objects=excluded.objects,chars=excluded.chars,updated_at=CURRENT_TIMESTAMP''',
                    (file_id, content_hash, VERSION, self.name, self.sections, self.objects, self.chars))

    def discard(self):
        self.finish()
        self.path.unlink(missing_ok=True)


def structures(text):
    """Conservative text structure navigation, never inferred field semantics."""
    # Mask comments and strings before balancing braces; preserve coordinates.
    masked = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)
    for match in re.finditer(r'\b(struct|class|enum)\s+(?:class\s+)?([A-Za-z_]\w*)[^;{}]*\{', masked):
        depth, end = 1, match.end()
        while end < len(masked) and depth:
            depth += (masked[end] == '{') - (masked[end] == '}')
            end += 1
        if not depth and masked[end:end+1] == ';':
            end += 1
        members = []
        for field in re.finditer(r'(?m)^\s*[^\n;{}]+;', text[match.end():end]):
            members.append({'declaration': field[0].strip()[:400], 'start': match.end()+field.start()})
        yield match[1], match[2], match.start(), end, not depth, members
    headings = list(re.finditer(r'(?m)^(?:#{1,6}\s+|\d+(?:\.\d+)+\.?\s+)([^\n]+)', text))
    for i, match in enumerate(headings):
        yield 'heading', match[1][:300], match.start(), headings[i+1].start() if i+1 < len(headings) else len(text), False, []
    for match in re.finditer(r'(?m)^\|[^\n]+\n\|\s*:?-{3,}[^\n]*', text):
        end = match.end()
        while end < len(text):
            line_end = text.find('\n', end+1)
            if line_end < 0:
                line_end = len(text)
            if not text[end:line_end].strip().startswith('|'):
                break
            end = line_end
        yield 'table', text[match.start():text.find('\n', match.start())][:300], match.start(), end, False, []


def info(file):
    try:
        with db.get_cursor() as cur:
            row = cur.execute('SELECT * FROM document_read_indexes WHERE file_id=?', (file['id'],)).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        return None
    if not row:
        return None
    row = dict(row)
    if row['content_hash'] != file['content_hash'] or row['format_version'] != VERSION:
        return None
    if not (root() / row['artifact']).is_file():
        return None
    return row


@contextmanager
def open_index(file):
    value = info(file)
    if not value:
        yield None
        return
    conn = sqlite3.connect((root()/value['artifact']).as_uri()+'?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def build_file(file, check=lambda: None, progress=lambda detail: None):
    """Manual rebuilding never changes source text or vectors silently."""
    from src.knowledge.parsers.registry import parse_file
    from src.knowledge.parsers.xlsx_parser import iter_xlsx_sections
    from src.knowledge.parsers.base import ParsedSection
    path = Path(file['absolute_path'])
    if not path.is_file():
        base = db.settings.feed_folder if file['kb_id']=='default' else db.settings.feed_folder/file['kb_id']
        for location in (base,db.settings.feed_folder):
            candidate = (location/file['relative_path']).resolve()
            if candidate.is_relative_to(base.resolve()) and candidate.is_file():
                path = candidate
                break
    if not path.is_file():
        raise ValueError('原文件不可用，无法恢复完整原文；已有检索仍保留')
    if fingerprint(path, check) != file['content_hash']:
        raise ValueError('原文件已变化，请重新导入后再建索引，避免新旧正文混用')
    builder = Builder()
    try:
        if path.suffix.lower() in ('.xlsx', '.xlsm'):
            from contextlib import closing
            with closing(iter_xlsx_sections(path, lambda *a: check())) as sections:
                for section in sections:
                    check()
                    builder.add(section)
                    progress(f'已处理 {builder.sections} 个章节')
        elif path.suffix.lower() in ('.h', '.hpp', '.c', '.cpp', '.cc', '.py', '.js', '.ts', '.java', '.cs'):
            # Keep code objects across the old 500-line storage boundaries.
            check()
            builder.add(ParsedSection(path.read_text('utf-8', errors='replace'), path, 0, '完整代码文档'))
        else:
            document = parse_file(path)
            for section in document.sections:
                check()
                builder.add(section)
                progress(f'已处理 {builder.sections} 个章节')
        if fingerprint(path, check) != file['content_hash']:
            raise ValueError('文件在建索引期间发生变化，请重试导入')
        builder.finish()
        return builder
    except BaseException:
        builder.discard()
        raise


def statuses(kb_id):
    files = db.list_files_in_kb(kb_id)
    with db.get_cursor() as cur:
        indexed = {row['file_id']:dict(row) for row in cur.execute('SELECT i.* FROM document_read_indexes i JOIN knowledge_files f ON f.id=i.file_id WHERE f.kb_id=?',(kb_id,))}
    folder = root()
    result = []
    for file in files:
        value = indexed.get(file['id'])
        if value and (value['content_hash']!=file['content_hash'] or value['format_version']!=VERSION or not (folder/value['artifact']).is_file()):
            value = None
        result.append({'id': file['id'], 'name': file['relative_path'],
                       'status': 'ready' if value else 'missing' if file['status']=='done' else 'unavailable',
                       'sections': value['sections'] if value else 0, 'objects': value['objects'] if value else 0})
    return result
