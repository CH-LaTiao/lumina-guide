import sqlite3,sys,tarfile,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from backup_runtime import backup

class BackupTests(unittest.TestCase):
    def test_active_wal_snapshot_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.mkdir();db=sqlite3.connect(runtime/'data.sqlite3')
            try:
                db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE sample(x)');db.execute('INSERT INTO sample VALUES(42)');db.commit()
                (runtime/'preview.pid').write_text('123');(runtime/'live-view.log').write_text('not evidence')
                (runtime/'lan-access.json').write_text('{"code":"private-access-code"}')
                output=root/'evidence.tar.gz';result=backup(runtime,output)
                self.assertEqual(result['files'],1)
                with tarfile.open(output) as tar:
                    # Extract only this known regular-file member in the temporary test folder.
                    content=tar.extractfile('runtime/data.sqlite3').read()
                    self.assertNotIn('runtime/data.sqlite3-wal',tar.getnames())
                restored=root/'restored.sqlite3';restored.write_bytes(content);other=sqlite3.connect(restored)
                try:self.assertEqual(other.execute('SELECT x FROM sample').fetchone()[0],42)
                finally:other.close()
                with self.assertRaises(ValueError):backup(runtime,output)
            finally:db.close()
if __name__=='__main__':unittest.main()
