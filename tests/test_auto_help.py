import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from auto_help import AutoHelp,safe_auto_speech


def status(events=1,transition=None):
    return {'state':'STREAMING','frame_age_ms':60,'session_id':'s',
        'online':{'busy':False,'state':'IDLE'},
        'guidance':{'event_id':events,'action':'STOP','priority':100,'candidate':None,
            'ground_transition':transition}}


class AutoHelpTests(unittest.TestCase):
    def test_requires_explicit_consent(self):
        with self.assertRaises(ValueError):AutoHelp().configure(True,False)
        self.assertFalse(AutoHelp().read()['enabled'])

    def test_step_uncertainty_requires_stability_and_cooldown(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        self.assertIsNone(helper.evaluate(status(transition='STEP_UP_OR_CURB')))
        now[0]=11.6
        self.assertEqual(helper.evaluate(status(transition='STEP_UP_OR_CURB'))['reason'],'step_uncertainty')
        helper.note_submitted('r1')
        self.assertIsNone(helper.evaluate(status(2,transition='STEP_UP_OR_CURB')))

    def test_blocked_no_route_waits_longer(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        helper.evaluate(status())
        now[0]=12.0;self.assertIsNone(helper.evaluate(status()))
        now[0]=12.6;self.assertEqual(helper.evaluate(status())['scene'],'obstacle')

    def test_candidate_blocks_another_upload(self):
        value=status();value['online']={'busy':False,'state':'CANDIDATE'}
        self.assertIsNone(AutoHelp().evaluate(value))

    def test_busy_gap_restarts_hold_without_crashing(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        helper.evaluate(status())
        now[0]=10.1;busy=status();busy['online']={'busy':True,'state':'RUNNING'}
        self.assertIsNone(helper.evaluate(busy))
        now[0]=10.2;self.assertIsNone(helper.evaluate(status()))
        now[0]=12.8;self.assertEqual(helper.evaluate(status())['scene'],'obstacle')

    def test_standing_near_object_can_trigger_with_standing_freshness(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        value=status();value['frame_age_ms']=350
        value['guidance']={'event_id':4,'mode':'STANDING','action':'NEAR_OBJECT',
            'priority':100,'candidate':None,'ground_transition':None}
        helper.evaluate(value);now[0]=12.6
        self.assertEqual(helper.evaluate(value)['scene'],'obstacle')

    def test_only_safe_review_guidance_is_spoken(self):
        result={'state':'CANDIDATE','gate':'REVIEW_ONLY','safety_flags':[],
            'plan':{'next_step':{'kind':'observe','say':'请先确认台阶高度。'}}}
        self.assertEqual(safe_auto_speech(result),'请先确认台阶高度。')
        result['gate']='BLOCKED_MOVEMENT';self.assertIsNone(safe_auto_speech(result))
        result['gate']='REVIEW_ONLY';result['safety_flags']=[{'code':'x'}];self.assertIsNone(safe_auto_speech(result))


if __name__=='__main__':unittest.main()
