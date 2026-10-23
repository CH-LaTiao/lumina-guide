import json, sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from replay_eval import evaluate, ROOT

SCHEMA=json.loads((ROOT/'prompts/online/plan.schema.json').read_text(encoding='utf-8'))
BASE=json.loads((ROOT/'prompts/online/plan.example.json').read_text(encoding='utf-8'))


class ReplayEvalTests(unittest.TestCase):
    def test_counts_flags_and_gate(self):
        plan=dict(BASE)
        plan['next_step']=dict(BASE['next_step'],say='前方没有障碍，可以安全通行')
        records=[
            {'request_id':'a','scene':'general','result':{'gate':'REVIEW_ONLY','plan':plan,'safety_flags':[]}},
            {'request_id':'b','scene':'general','result':{'state':'FAILED'}},
        ]
        report=evaluate(records,SCHEMA)
        self.assertEqual(report['records'],2)
        self.assertEqual(report['with_plan'],1)
        self.assertEqual(report['schema_valid'],1)
        self.assertIn('absolute_clear_claim',report['flags_now'])
        self.assertEqual(report['plans_with_changed_flags'],1)
        self.assertEqual(report['gates'],{'REVIEW_ONLY':1})

    def test_identical_flags_are_not_counted_as_changed(self):
        plan=dict(BASE)
        records=[{'request_id':'a','scene':'general','result':{'gate':'REVIEW_ONLY','plan':plan,'safety_flags':[]}}]
        self.assertEqual(evaluate(records,SCHEMA)['plans_with_changed_flags'],0)


if __name__=='__main__':unittest.main()
