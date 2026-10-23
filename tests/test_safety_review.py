import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from safety_review import review

def sample(say,kind='observe',goal='观察前方'):
    return {'task_goal':goal,'observations':[{'fact':'画面有门'}],'plan':['继续观察'],
        'next_step':{'kind':kind,'say':say,'conditions':[],'evidence_ids':[]}}

class SafetyReviewTests(unittest.TestCase):
    def codes(self,plan):return {f['code'] for f in review(plan)}
    def test_absolute_clear_claim_flagged(self):
        self.assertIn('absolute_clear_claim',self.codes(sample('前方没有障碍，可以安全通行')))
    def test_negated_uncertainty_not_flagged(self):
        self.assertEqual(self.codes(sample('入口尚未确认，不能判断是否可以通行')),set())
    def test_movement_wording_flagged_outside_move_candidate(self):
        self.assertIn('movement_in_prose',self.codes(sample('请向左走三步')))
    def test_move_candidate_is_gated_not_flagged_as_prose(self):
        self.assertNotIn('movement_in_prose',self.codes(sample('向左走三步',kind='move_candidate')))
    def test_certainty_claim_flagged(self):
        self.assertIn('certainty_claim',self.codes(sample('保证没有问题')))
    def test_crossing_permission_flagged(self):
        self.assertIn('movement_permission',self.codes(sample('绿灯了，可以过马路')))
    def test_following_a_crowd_flagged(self):
        self.assertIn('follow_crowd',self.codes(sample('跟着人群走就行')))
    def test_confirmed_safety_claim_flagged(self):
        self.assertIn('confirmed_safety_claim',self.codes(sample('已经确认前方安全')))
    def test_unconfirmed_safety_is_not_flagged(self):
        self.assertEqual(self.codes(sample('还没有确认是否安全，请继续观察')),set())

if __name__=='__main__':unittest.main()
