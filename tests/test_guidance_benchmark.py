import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from guidance_benchmark import run


class GuidanceBenchmarkTests(unittest.TestCase):
    def test_all_deterministic_policy_scenarios_pass(self):
        report=run()
        self.assertTrue(report['passed'],[case for case in report['cases'] if not case['passed']])
        self.assertEqual(report['algorithm'],'guidance-benchmark-4')
        self.assertEqual(report['counts'],{'total':19,'passed':19})


if __name__=='__main__':unittest.main()
