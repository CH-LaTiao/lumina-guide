"""UTF-8 CSV round-trip for Excel review; immutable originals and append-only revisions.

Import creates review drafts only. It never trains a model or activates a prompt.
"""
import argparse
import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from runtime_paths import runtime_dir

ORIGINAL = ['request_id', 'created_utc', 'scene', 'question', 'prompt', 'raw_response', 'result']
EDITS = ['corrected_scene', 'corrected_answer', 'corrected_prompt', 'review_note']
FIELDS = ['request_id', 'source_sha256', 'base_revision'] + ORIGINAL[1:] + EDITS
SCENES = {'general', 'find_target', 'obstacle', 'metro', 'stairs', 'traffic_signal', 'read_sign'}

def digest(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def safe_cell(value):
    text = str(value or '')
    return "'" + text if text.startswith(("'", '\t', '\r', '\n')) or text.lstrip().startswith(('=', '+', '-', '@')) else text

def restore_cell(text):
    if text.startswith("'") and safe_cell(text[1:]) == text:
        return text[1:]
    return text

class ReviewStore:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else runtime_dir() / 'queries.sqlite3'
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = self.connect()
        try:
            db.execute('CREATE TABLE IF NOT EXISTS queries(request_id TEXT PRIMARY KEY,created_utc TEXT,scene TEXT,question TEXT,prompt TEXT,raw_response TEXT,result TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS reviews(
                id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, revision TEXT NOT NULL UNIQUE,
                source_sha256 TEXT NOT NULL, base_revision TEXT NOT NULL,
                corrected_scene TEXT NOT NULL, corrected_answer TEXT NOT NULL,
                corrected_prompt TEXT NOT NULL, review_note TEXT NOT NULL, created_utc TEXT NOT NULL)''')
            db.commit()
        finally:
            db.close()

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    def export_csv(self):
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, FIELDS)
        writer.writeheader()
        db = self.connect()
        try:
            for source in db.execute('SELECT * FROM queries ORDER BY created_utc,request_id'):
                original = {k: source[k] or '' for k in ORIGINAL}
                revision = db.execute('SELECT * FROM reviews WHERE request_id=? ORDER BY id DESC LIMIT 1', (source['request_id'],)).fetchone()
                row = dict(original, source_sha256=digest(original), base_revision=revision['revision'] if revision else '')
                row.update({k: revision[k] if revision else '' for k in EDITS})
                writer.writerow({k: safe_cell(row[k]) for k in FIELDS})
        finally:
            db.close()
        return ('\ufeff' + buffer.getvalue()).encode('utf-8')

    def import_csv(self, data):
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('CSV exceeds 2 MiB; import a smaller batch')
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV headers changed; use the exported UTF-8 CSV')
        try:
            rows = list(reader)
        except csv.Error as exc:
            raise ValueError('Invalid CSV or oversized field') from exc
        if len(rows) > 2000:
            raise ValueError('At most 2000 rows per import')
        seen, imported, skipped = set(), 0, 0
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                for index, row in enumerate(rows, 2):
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError(f'Row {index}: column count changed')
                    row = {k: restore_cell(v) for k, v in row.items()}
                    rid = row['request_id']
                    if rid in seen:
                        raise ValueError(f'Row {index}: duplicate request_id')
                    seen.add(rid)
                    source = db.execute('SELECT * FROM queries WHERE request_id=?', (rid,)).fetchone()
                    if source is None:
                        raise ValueError(f'Row {index}: unknown request_id')
                    original = {k: source[k] or '' for k in ORIGINAL}
                    sha = digest(original)
                    if row['source_sha256'] != sha or digest({k: row[k] for k in ORIGINAL}) != sha:
                        raise ValueError(f'Row {index}: original fields changed; edit only corrected_* and review_note')
                    edits = {k: row[k] for k in EDITS}
                    if not any(edits.values()):
                        skipped += 1
                        continue
                    if any(len(v) > 12000 for v in edits.values()):
                        raise ValueError(f'Row {index}: review text exceeds 12000 characters')
                    if edits['corrected_scene'] and edits['corrected_scene'] not in SCENES:
                        raise ValueError(f'Row {index}: invalid corrected_scene')
                    revision = digest([rid, sha, row['base_revision'], edits])
                    if db.execute('SELECT 1 FROM reviews WHERE revision=?', (revision,)).fetchone():
                        skipped += 1
                        continue
                    latest = db.execute('SELECT * FROM reviews WHERE request_id=? ORDER BY id DESC LIMIT 1', (rid,)).fetchone()
                    if latest and all(latest[k] == edits[k] for k in EDITS) and row['base_revision'] == latest['revision']:
                        skipped += 1
                        continue
                    if row['base_revision'] != (latest['revision'] if latest else ''):
                        raise ValueError(f'Row {index}: revision conflict; export again before editing')
                    db.execute("INSERT INTO reviews(request_id,revision,source_sha256,base_revision,corrected_scene,corrected_answer,corrected_prompt,review_note,created_utc) VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                               (rid, revision, sha, row['base_revision'], *(edits[k] for k in EDITS)))
                    imported += 1
        finally:
            db.close()
        return {'imported': imported, 'skipped': skipped, 'status': 'DRAFT_REVIEW_ONLY', 'prompts_activated': 0}

    def summary(self):
        db = self.connect()
        try:
            return {'queries': db.execute('SELECT count(*) FROM queries').fetchone()[0],
                    'revisions': db.execute('SELECT count(*) FROM reviews').fetchone()[0],
                    'prompt_candidates': db.execute("SELECT count(*) FROM reviews WHERE corrected_prompt<>''").fetchone()[0],
                    'activation': 'manual_review_required'}
        finally:
            db.close()

    def candidates(self):
        db = self.connect()
        try:
            rows = db.execute('''SELECT r.*,q.scene FROM reviews r JOIN queries q USING(request_id)
                WHERE r.id IN(SELECT max(id) FROM reviews GROUP BY request_id) AND corrected_prompt<>'' ORDER BY r.id''')
            return {'status': 'DRAFT_NOT_ACTIVE', 'items': [
                {'request_id': r['request_id'], 'revision': r['revision'], 'source_sha256': r['source_sha256'],
                 'scene': r['corrected_scene'] or r['scene'], 'prompt_candidate': r['corrected_prompt'],
                 'expected_answer': r['corrected_answer'], 'review_note': r['review_note']} for r in rows]}
        finally:
            db.close()

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['export', 'import', 'candidates', 'status'])
    p.add_argument('--db', type=Path)
    p.add_argument('--file', type=Path)
    a = p.parse_args()
    store = ReviewStore(a.db)
    if a.action in ('export', 'import', 'candidates') and not a.file:
        p.error('--file required')
    if a.action == 'export':
        with a.file.open('xb') as f:
            f.write(store.export_csv())
    elif a.action == 'import':
        print(json.dumps(store.import_csv(a.file.read_bytes()), ensure_ascii=False))
    elif a.action == 'candidates':
        with a.file.open('x', encoding='utf-8') as f:
            json.dump(store.candidates(), f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(store.summary()))
