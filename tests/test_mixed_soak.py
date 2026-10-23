import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from types import SimpleNamespace
import mixed_soak


class MixedSoakTests(unittest.TestCase):
    def test_write_report_counts_query_states(self):
        import tempfile
        args=SimpleNamespace(minutes=1)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'mixed.json'
            report=mixed_soak.write(target,'2026-10-18T00:00:00Z',args,
                [{'state':'STREAMING'}],[],[{'state':'CANDIDATE'},{'state':'TIMEOUT'}],3,1,True)
            self.assertEqual(report['query_states'],{'CANDIDATE':1,'TIMEOUT':1})
            self.assertEqual(report['frames_ok'],3)
            self.assertTrue(report['final'])
            self.assertTrue(target.exists())


if __name__=='__main__':unittest.main()
