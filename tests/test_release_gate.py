import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from release_gate import status_checks


def status():
    return {
        'state':'STREAMING','frame_age_ms':100,
        'software':{'version':'v9.9.9','commit':'abc','source':'release','dirty':False,
                    'code_root':'/release'},
        'guidance':{'movement_allowed':False},
        'readiness':{'stage':'FIELD_VALIDATION','ready_for_movement':False,'checks':{'x':{'ok':True}},
                     'blockers':['场地验收尚未完成']},
    }


class ReleaseGateTests(unittest.TestCase):
    def test_live_gate_requires_release_provenance_and_locked_movement(self):
        value=status_checks(status())
        self.assertTrue(value['passed'])
        self.assertEqual(value['version'],'v9.9.9')
        self.assertEqual(value['readiness_stage'],'FIELD_VALIDATION')

    def test_movement_or_nonrelease_source_fails_gate(self):
        value=status();value['software']['source']='working-tree';value['guidance']['movement_allowed']=True
        value['frame_age_ms']=250
        result=status_checks(value)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['release_source'])
        self.assertFalse(result['checks']['movement_locked'])
        self.assertFalse(result['checks']['camera_fresh'])


if __name__=='__main__':unittest.main()
