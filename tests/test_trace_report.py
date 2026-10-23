import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from route_trace import make_record
from trace_report import load, report


def status(frame='s:1'):
    return {
        'state':'STREAMING','session_id':'s','frame_id':frame,'frame_age_ms':90,
        'software_version':'v9.9.9','software':{'commit':'abc','source':'release','code_root':'/release'},
        'geometry':{'ground_state':'UNKNOWN','body_calibration':{'state':'UNSET'}},
        'detection':{'provider':'CUDA'},'readiness':{'stage':'GROUND_REFERENCE','ready_for_movement':False},
        'guidance_analysis':{},'guidance':{'direction_frame':'CAMERA','action':'NEAR_OBJECT',
            'priority':100,'speech':'画面两侧有近障碍。','candidate':None,'event_id':1,
            'movement_allowed':False,'corridors':[],'reasons':[]},
    }


class TraceReportTests(unittest.TestCase):
    def test_report_ties_trace_to_version_and_locked_gate(self):
        first=make_record(status('s:1'),'open');second=make_record(status('s:2'),'open')
        second['wall_time']=first['wall_time']+1
        value=report([first,second])
        self.assertTrue(value['passed'])
        self.assertEqual(value['software_versions'],['v9.9.9'])
        self.assertEqual(value['software_commits'],['abc'])
        self.assertEqual(value['records'],2)
        self.assertEqual(value['speech_transitions'],1)

    def test_movement_release_or_mixed_versions_fail_audit(self):
        first=make_record(status('s:1'),'open');second=make_record(status('s:2'),'open')
        second['movement_allowed']=True;second['software_version']='v9.9.10'
        value=report([first,second])
        self.assertFalse(value['passed'])
        self.assertFalse(value['checks']['movement_locked'])
        self.assertFalse(value['checks']['single_software_version'])
        self.assertEqual(value['movement_true_records'],1)

    def test_loader_rejects_empty_and_invalid_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty=Path(tmp)/'empty.jsonl';empty.write_text('',encoding='utf-8')
            with self.assertRaises(ValueError):load(empty)
            bad=Path(tmp)/'bad.jsonl';bad.write_text('{bad}\n',encoding='utf-8')
            with self.assertRaises(ValueError):load(bad)
            good=Path(tmp)/'good.jsonl';good.write_text(json.dumps(make_record(status(),'x'))+'\n',encoding='utf-8')
            self.assertEqual(len(load(good)),1)


if __name__=='__main__':unittest.main()
