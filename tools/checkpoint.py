"""Transactional progress journal; completed steps are skipped only if evidence still matches."""
import argparse, hashlib, json, sqlite3
from pathlib import Path
from datetime import datetime, timezone
p=argparse.ArgumentParser();p.add_argument('action',choices=['done','resume']);p.add_argument('--step');p.add_argument('--evidence',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]; state=root/'runtime';state.mkdir(exist_ok=True)
c=sqlite3.connect(state/'progress.sqlite3')
c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,step TEXT,evidence TEXT,sha256 TEXT,utc TEXT)')
if a.action=='done':
 if not a.step or not a.evidence or not a.evidence.is_file():p.error('done requires step and existing evidence')
 path=a.evidence.resolve();sha=hashlib.sha256(path.read_bytes()).hexdigest()
 last=c.execute('SELECT sha256 FROM events WHERE step=? ORDER BY id DESC LIMIT 1',(a.step,)).fetchone()
 if not last or last[0]!=sha:
  with c:c.execute('INSERT INTO events(step,evidence,sha256,utc) VALUES(?,?,?,?)',(a.step,str(path),sha,datetime.now(timezone.utc).isoformat()))
for step,evidence,sha in c.execute('SELECT step,evidence,sha256 FROM events WHERE id IN (SELECT max(id) FROM events GROUP BY step)'):
 f=Path(evidence);valid=f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==sha
 print(json.dumps({'step':step,'status':'DONE_SKIP' if valid else 'EVIDENCE_CHANGED_RECHECK'},ensure_ascii=False))
c.close()
