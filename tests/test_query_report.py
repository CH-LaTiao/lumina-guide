import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from query_report import summarise

class QueryReportTests(unittest.TestCase):
    def test_summary_counts_states_flags_and_latency(self):
        records=[
            {'scene':'general','result':{'state':'CANDIDATE','elapsed_ms':9000,'ids_normalized':True,
                'review':True,'safety_flags':[{'code':'absolute_clear_claim'}],
                'plan':{'observations':[{},{}],'missing_information':['距离'],'next_step':{'kind':'observe'}}}},
            {'scene':'general','result':{'state':'EXPIRED','elapsed_ms':11000,'safety_flags':[]}},
            {'scene':'metro','result':{'state':'TIMEOUT','error':'DEADLINE'}},
        ]
        report=summarise(records)
        self.assertEqual(report['total'],3)
        self.assertEqual(report['states'],{'CANDIDATE':1,'EXPIRED':1,'TIMEOUT':1})
        self.assertEqual(report['scenes'],{'general':2,'metro':1})
        self.assertEqual(report['ids_normalized'],1)
        self.assertEqual(report['review'],1)
        self.assertEqual(report['safety_flags'],{'absolute_clear_claim':1})
        self.assertEqual(report['next_step_kinds'],{'observe':1})
        self.assertEqual(report['observations_median'],2)
        self.assertEqual(report['missing_information_median'],1)
        self.assertEqual(report['elapsed_ms']['median'],10000)

if __name__=='__main__':unittest.main()
