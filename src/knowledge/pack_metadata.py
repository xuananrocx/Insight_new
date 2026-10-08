"""Optional derived metadata in v1 packs; IDs are remapped on import."""
import json
import hashlib
from src.db import metadata_db as db
from src.knowledge.pack_validation import portable_path


def export_metadata(kb_id, files):
    file_ids = {f['id'] for f in files}
    with db.get_cursor() as cur:
        docs = [dict(r) for r in cur.execute('SELECT * FROM document_meta WHERE kb_id=?', (kb_id,)) if r['file_id'] in file_ids]
        concepts = [dict(r) for r in cur.execute('SELECT * FROM kb_concepts WHERE kb_id=?', (kb_id,))]
        relations = [dict(r) for r in cur.execute('SELECT * FROM concept_relations WHERE kb_id=?', (kb_id,))]
    return {'documents': docs, 'concepts': concepts, 'relations': relations,
            'global_summary': db.get_kb_global_summary(kb_id)}


def restore_metadata(manifest, kb_id, paths):
    data = manifest.get('derived_metadata')
    if not data:
        return  # Original packs contain no derived metadata.
    ids = {int(f['original_file_id']): paths[portable_path(f['relative_path'])]
           for f in manifest['files'] if 'original_file_id' in f}
    with db.get_cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        try:
            for row in data.get('documents', []):
                if row['file_id'] not in ids:
                    continue
                columns = ['processed_level', 'summary', 'summary_model', 'summary_created_at', 'summary_tokens',
                           'concepts_extracted', 'concepts_count', 'doc_type', 'section_count', 'total_chunks',
                           'word_count', 'created_at', 'updated_at']
                cur.execute('INSERT OR REPLACE INTO document_meta (file_id,kb_id,' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in range(len(columns)+2)) + ')',
                            [ids[row['file_id']], kb_id, *[row.get(c) for c in columns]])
            concepts = {}
            for row in data.get('concepts', []):
                linked = list(dict.fromkeys(ids[f] for f in json.loads(row['source_file_ids_json']) if f in ids))
                if not linked:
                    continue
                cur.execute('INSERT INTO kb_concepts (kb_id,concept_name,concept_type,description,source_file_ids_json,mention_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                            (kb_id, row['concept_name'], row.get('concept_type'), row.get('description'), json.dumps(linked), len(linked), row['created_at'], row['updated_at']))
                concepts[row['id']] = cur.lastrowid
            for row in data.get('relations', []):
                if row['source_concept_id'] not in concepts or row['target_concept_id'] not in concepts:
                    continue
                def remap(value, key=''):
                    if isinstance(value, dict):
                        return {k: remap(v, k) for k, v in value.items()}
                    if isinstance(value, list):
                        return [remap(v, key.removesuffix('s')) for v in value]
                    if key == 'file_id':
                        return ids.get(value)
                    if key == 'chunk_id':
                        return hashlib.sha256(f'{kb_id}:{value}'.encode()).hexdigest()
                    if key == 'kb_id':
                        return kb_id
                    return value
                cur.execute('INSERT INTO concept_relations (kb_id,source_concept_id,target_concept_id,relation_type,weight,evidence_json,created_at) VALUES (?,?,?,?,?,?,?)',
                            (kb_id, concepts[row['source_concept_id']], concepts[row['target_concept_id']], row['relation_type'], row.get('weight', 1), json.dumps(remap(json.loads(row.get('evidence_json') or '[]')), ensure_ascii=False), row['created_at']))
            cur.execute('COMMIT')
        except BaseException:
            cur.execute('ROLLBACK')
            raise
    summary = data.get('global_summary')
    if summary and summary.get('summary'):
        old_snapshot = summary.get('snapshot') or {}
        expected = {str(f['original_file_id']): f.get('content_hash') for f in manifest['files'] if 'original_file_id' in f}
        snapshot = {str(ids[int(fid)]): value for fid, value in old_snapshot.items()} if old_snapshot == expected else None
        db.update_kb_global_summary(kb_id, summary['summary'], model=summary.get('model') or '',
                                   tokens=summary.get('tokens') or 0, snapshot=snapshot)
