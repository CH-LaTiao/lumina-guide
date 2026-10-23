"""Consistent SQLite snapshots and runtime evidence, separate from code releases."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from runtime_paths import runtime_dir

def backup(source, output):
    source=source.resolve();output=output.resolve()
    if output.exists():raise ValueError('Backup already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lumina-evidence-') as tmp:
        dest=Path(tmp)/'runtime';dest.mkdir()
        for path in source.iterdir():
            if path.name=='lan-access.json':continue
            if not path.is_file() or path.is_symlink() or path.name.endswith(('-wal','-shm','-journal','.pid','.lock','.log')):continue
            if path.suffix=='.sqlite3':
                src=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);target=sqlite3.connect(dest/path.name)
                try:src.backup(target)
                finally:target.close();src.close()
            elif path.suffix in ('.json','.csv','.png','.jpg','.txt'):
                shutil.copy2(path,dest/path.name)
        manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir()}
        (Path(tmp)/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        # Exclusive destination creation: never replace an earlier evidence archive.
        import tarfile
        with output.open('xb') as file:
            with tarfile.open(fileobj=file,mode='w:gz') as tar:
                tar.add(dest,arcname='runtime');tar.add(Path(tmp)/'manifest.json',arcname='manifest.json')
    return {'archive':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(manifest)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--runtime',type=Path,default=runtime_dir());a=p.parse_args()
    print(json.dumps(backup(a.runtime,a.output)))
