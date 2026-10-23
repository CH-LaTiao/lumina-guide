import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from soak import read_pid, write_report

class SoakTests(unittest.TestCase):
    def test_plain_and_json_pid_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'preview.pid'
            path.write_text('123\n')
            self.assertEqual(read_pid(path),123)
            path.write_text(json.dumps({'pid':456,'version':'working-tree'}))
            self.assertEqual(read_pid(path),456)
            path.write_text('')
            self.assertIsNone(read_pid(path))
    def test_partial_report_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'soak.json'
            write_report(target,'2026-10-18T00:00:00Z',1,5,[{'state':'STREAMING'}],[],final=True)
            data=json.loads(target.read_text(encoding='utf-8'))
            self.assertTrue(data['final'])
            self.assertEqual(data['summary']['samples'],1)

    def test_summary_includes_imu_and_local_tts(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'soak.json'
            write_report(target,'2026-10-18T00:00:00Z',1,5,[
                {'state':'STREAMING','gait_state':'STANDING','mode':'STANDING','imu_age_ms':5,
                 'tts_model':'zh_CN-huayan-medium.onnx','tts_cache_phrases':22}],[],final=True)
            summary=json.loads(target.read_text())['summary']
            self.assertEqual(summary['gait_states'],{'STANDING':1})
            self.assertEqual(summary['modes'],{'STANDING':1})
            self.assertEqual(summary['tts_models'],['zh_CN-huayan-medium.onnx'])
            self.assertEqual(summary['tts_cache_phrases']['median'],22)

if __name__=='__main__':unittest.main()
