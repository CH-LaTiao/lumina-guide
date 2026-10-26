import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from auto_help import AutoHelp,safe_auto_speech


def status(events=1,transition=None):
    return {'state':'STREAMING','frame_age_ms':60,'session_id':'s',
        'online':{'busy':False,'state':'IDLE'},
        'guidance':{'event_id':events,'action':'STOP','priority':100,'candidate':None,
            'mode':'STANDING','fresh':True,'ground_transition':transition}}


class AutoHelpTests(unittest.TestCase):
    def test_automatic_help_preserves_a_continuable_manual_task(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        value=status();value['online']['task']={'can_continue':True}
        self.assertIsNone(helper.evaluate(value));now[0]=20.
        self.assertIsNone(helper.evaluate(value));self.assertIsNone(helper.since)
        value['online']['task']['can_continue']=False
        self.assertIsNone(helper.evaluate(value));now[0]=22.6
        self.assertEqual(helper.evaluate(value)['scene'],'obstacle')

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
        helper=AutoHelp();helper.configure(True,True)
        self.assertIsNone(helper.evaluate(value))

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
            'priority':100,'candidate':None,'ground_transition':None,'fresh':True}
        helper.evaluate(value);now[0]=12.6
        self.assertEqual(helper.evaluate(value)['scene'],'obstacle')

    def test_paused_transition_never_uploads_and_resume_restarts_hold(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        value=status(transition='STEP_UP_OR_CURB')
        helper.evaluate(value)
        now[0]=11.4
        paused=status(transition='STEP_UP_OR_CURB');paused['guidance']['mode']='PAUSED'
        self.assertIsNone(helper.evaluate(paused))
        self.assertIsNone(helper.since);self.assertIsNone(helper.last_event)
        now[0]=20.;self.assertIsNone(helper.evaluate(paused))
        now[0]=21.;self.assertIsNone(helper.evaluate(value))
        now[0]=22.4;self.assertIsNone(helper.evaluate(value))
        now[0]=22.6;self.assertEqual(helper.evaluate(value)['scene'],'stairs')

    def test_missing_or_unknown_mode_cannot_trigger(self):
        for mode in (None,'AUTO','UNKNOWN','PAUSED',''):
            with self.subTest(mode=mode):
                now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
                value=status(transition='STEP_UP_OR_CURB');value['guidance']['mode']=mode
                helper.evaluate(value);now[0]=20.
                self.assertIsNone(helper.evaluate(value))
                self.assertIsNone(helper.last_event)

    def test_stale_or_missing_guidance_freshness_restarts_hold(self):
        for fresh in (None,False,1,'true'):
            with self.subTest(fresh=fresh):
                now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
                value=status(transition='STEP_UP_OR_CURB');helper.evaluate(value)
                value['guidance']['fresh']=fresh;now[0]=11.4
                self.assertIsNone(helper.evaluate(value))
                self.assertIsNone(helper.since);self.assertIsNone(helper.last_event)
                value['guidance']['fresh']=True;now[0]=12.
                self.assertIsNone(helper.evaluate(value))
                now[0]=13.6;self.assertEqual(helper.evaluate(value)['scene'],'stairs')

    def test_zero_frame_age_is_valid(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        value=status(transition='STEP_UP_OR_CURB');value['frame_age_ms']=0
        helper.evaluate(value);now[0]=11.6
        self.assertEqual(helper.evaluate(value)['scene'],'stairs')

    def test_invalid_camera_ages_are_rejected_without_carrying_hold(self):
        for age in (None,float('nan'),float('inf'),float('-inf'),True,False,'60',-1,501):
            with self.subTest(age=age):
                now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
                value=status(transition='STEP_UP_OR_CURB');helper.evaluate(value)
                value['frame_age_ms']=age;now[0]=12.
                self.assertIsNone(helper.evaluate(value))
                self.assertIsNone(helper.since);self.assertIsNone(helper.last_event)
                value['frame_age_ms']=60;now[0]=13.
                self.assertIsNone(helper.evaluate(value))

    def test_walking_uses_stricter_camera_freshness(self):
        now=[10.];helper=AutoHelp(clock=lambda:now[0]);helper.configure(True,True)
        value=status(transition='STEP_UP_OR_CURB')
        value['guidance']['mode']='WALKING';value['frame_age_ms']=201
        helper.evaluate(value);now[0]=12.
        self.assertIsNone(helper.evaluate(value));self.assertIsNone(helper.last_event)
        value['frame_age_ms']=200
        self.assertIsNone(helper.evaluate(value));now[0]=13.6
        self.assertEqual(helper.evaluate(value)['scene'],'stairs')

    def test_only_safe_review_guidance_is_spoken(self):
        result={'state':'CANDIDATE','gate':'REVIEW_ONLY','safety_flags':[],
            'plan':{'next_step':{'kind':'observe','say':'请先确认台阶高度。'}}}
        self.assertEqual(safe_auto_speech(result),'请先确认台阶高度。')
        result['gate']='BLOCKED_MOVEMENT';self.assertIsNone(safe_auto_speech(result))
        result['gate']='REVIEW_ONLY';result['safety_flags']=[{'code':'x'}];self.assertIsNone(safe_auto_speech(result))


if __name__=='__main__':unittest.main()
