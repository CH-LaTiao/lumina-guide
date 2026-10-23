import csv,io,sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from review_store import ReviewStore,FIELDS,safe_cell,restore_cell

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ReviewStore(Path(self.tmp.name)/'q.sqlite3')
        db=self.store.connect()
        try:
            with db:
                for rid in ('r1','r2'):
                    db.execute('INSERT INTO queries VALUES(?,?,?,?,?,?,?)',(rid,'2026-10-17','general','=1+1','提示词\n第二行','真实回答','{}'))
        finally:db.close()
    def rows(self):return list(csv.DictReader(io.StringIO(self.store.export_csv().decode('utf-8-sig'))))
    def encode(self,rows):
        f=io.StringIO(newline='');w=csv.DictWriter(f,FIELDS);w.writeheader();w.writerows(rows);return f.getvalue().encode('utf-8-sig')
    def test_roundtrip_idempotent_and_original_immutable(self):
        rows=self.rows();self.assertEqual(rows[0]['question'],"'=1+1")
        rows[0]['corrected_answer']='请先确认指示牌';rows[0]['corrected_prompt']='地铁场景要核对线路方向。'
        payload=self.encode(rows)
        self.assertEqual(self.store.import_csv(payload)['imported'],1)
        reopened=ReviewStore(self.store.db_path)
        self.assertEqual(reopened.import_csv(payload)['skipped'],2)
        self.assertEqual(reopened.import_csv(reopened.export_csv())['imported'],0)
        self.assertEqual(reopened.candidates()['status'],'DRAFT_NOT_ACTIVE')
        self.assertEqual(self.rows()[0]['raw_response'],'真实回答')
    def test_conflict_rolls_back_entire_batch(self):
        rows=self.rows();rows[0]['corrected_answer']='修改';rows[1]['raw_response']='篡改原文'
        with self.assertRaises(ValueError):self.store.import_csv(self.encode(rows))
        self.assertEqual(self.store.summary()['revisions'],0)
    def test_concurrent_revision_rejected(self):
        rows=self.rows();rows[0]['review_note']='first';self.store.import_csv(self.encode(rows))
        rows[0]['review_note']='stale competing edit'
        with self.assertRaises(ValueError):self.store.import_csv(self.encode(rows))
        current=self.rows();current[0]['review_note']='second';self.store.import_csv(self.encode(current))
        self.assertEqual(self.store.summary()['revisions'],2)
    def test_csv_formula_and_apostrophe_roundtrip(self):
        for value in ['=HYPERLINK("bad")',' +cmd','-1','@SUM(A1)','\tformula',"'literal",'普通中文']:
            self.assertEqual(restore_cell(safe_cell(value)),value)
            self.assertFalse(safe_cell(value).lstrip().startswith(('=','+','-','@')))
    def test_invalid_scene_duplicate_and_oversize_rejected(self):
        rows=self.rows();rows[0]['corrected_scene']='made-up'
        with self.assertRaises(ValueError):self.store.import_csv(self.encode(rows))
        with self.assertRaises(ValueError):self.store.import_csv(self.encode([rows[1],rows[1]]))
        with self.assertRaises(ValueError):self.store.import_csv(b'x'*(2*1024*1024+1))
        self.assertEqual(self.store.summary()['revisions'],0)
if __name__=='__main__':unittest.main()
