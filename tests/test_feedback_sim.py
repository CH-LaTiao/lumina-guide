import sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from feedback_sim import FeedbackSimulator
from guidance import Guidance

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=10.;self.f=FeedbackSimulator(Path(self.tmp.name)/'f.sqlite3',clock=lambda:self.now)
    def info(self,**values):
        g=dict(mode='STANDING',fresh=True,action='NEAR_OBJECT',speech='左侧有物。',
            priority=100,event_id=1,frame_id='s:1',direction_frame='CAMERA',hazard_regions=[0])
        g.update(values)
        return {'state':'STREAMING','frame_age_ms':350,'guidance':g,
            'native_depth':[{'level':1},{'level':3},{'level':3}]}
    def active(self):return [r['channel'] for r in self.f.read()['haptics'] if r['active']]
    def test_repeated_event_uses_accepted_regions_not_new_raw_noise(self):
        for i in range(50):
            self.now+=.05;self.f.update(self.info(frame_id=f's:{i}'))
            self.assertEqual(self.active(),[0,1])
            self.assertEqual(self.f.read()['speech']['text'],'左侧有物。')
        self.assertEqual(len(self.f.read()['events']),1)
        db=sqlite3.connect(self.f.db_path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM feedback_events').fetchone()[0],1)
        finally:db.close()
    def test_same_direction_text_does_not_duplicate_log_for_new_event_id(self):
        self.f.update(self.info(event_id=1,frame_id='s:1'))
        self.f.update(self.info(event_id=2,frame_id='s:2'))
        state=self.f.read()
        self.assertEqual(len(state['events']),1)
        self.assertEqual(state['speech']['guidance_event_id'],2)
    def test_route_and_stop_interrupt_match_guidance(self):
        self.f.update(self.info(mode='WALKING',action='DIRECTION_CANDIDATE',speech='右侧可走。',
            candidate='RIGHT',direction_frame='BODY',can_continue=True))
        self.assertEqual(self.active(),[4,5]);self.assertEqual(self.f.read()['speech']['direction'],'RIGHT')
        self.f.update(self.info(mode='WALKING',action='STOP',speech='停，前方有障碍。',event_id=2,hazard_regions=[1]))
        self.assertEqual(self.active(),[2,3]);self.assertIsNone(self.f.read()['speech']['direction'])
    def test_camera_route_never_becomes_body_turn_vibration(self):
        self.f.update(self.info(action='DIRECTION_CANDIDATE',speech='左侧可走。',candidate='LEFT',can_continue=True))
        self.assertEqual(self.active(),[]);self.assertIsNone(self.f.read()['speech']['direction'])
    def test_camera_hazard_speech_carries_action_direction(self):
        self.f.update(self.info(speech='左侧近物，走中。',direction_hint='CENTER'))
        speech=self.f.read()['speech']
        self.assertEqual(speech['text'],'左侧近物，走中。')
        self.assertEqual(speech['direction'],'CENTER')
        self.assertEqual(speech['mapping'],'camera_regions')
        self.assertEqual(self.active(),[0,1])
    def test_raw_regions_alone_do_not_invent_route(self):
        self.f.update({'state':'STREAMING','frame_age_ms':20,'native_depth':[{'level':v} for v in (1,3,1)]})
        self.assertIsNone(self.f.read()['speech']);self.assertEqual(self.active(),[])
    def test_disconnect_discards_old_direction_immediately(self):
        value=self.info(mode='WALKING',action='DIRECTION_CANDIDATE',speech='左侧可走。',
            candidate='LEFT',direction_frame='BODY',can_continue=True)
        self.f.update(value);value['state']='RECONNECTING';self.f.update(value)
        s=self.f.read()['speech'];self.assertEqual(s['text'],'停，感知不可用。')
        self.assertIsNone(s['direction']);self.assertEqual(self.active(),[])
    def test_pause_expiry_and_silence_clear_outputs(self):
        self.f.update(self.info());self.f.pause(True);self.f.update(self.info())
        self.assertIsNone(self.f.read()['speech']);self.assertEqual(self.active(),[])
        self.f.pause(False);self.assertIsNone(self.f.read()['speech'])
        self.f.update(self.info());self.now+=.61
        self.assertIsNone(self.f.read()['speech']);self.assertEqual(self.active(),[])
        self.f.update(self.info(speech=''));self.assertIsNone(self.f.read()['speech'])
    def test_standing_boundary_jitter_is_one_spoken_and_haptic_event(self):
        g=Guidance(clock=lambda:self.now)
        for i in range(30):
            levels=(3,1,1) if i%2==0 else (1,1,1)
            value={'state':'STREAMING','frame_age_ms':300,'session_id':'s','frame_id':f's:{i}',
                'native_depth':[{'level':x} for x in levels]}
            value['guidance']=g.update(value);self.f.update(value);self.now+=.05
            state=self.f.read();self.assertEqual(self.active(),[0,1])
            self.assertEqual(state['speech']['text'],value['guidance']['speech'])
            self.assertEqual(state['speech']['direction'],'CENTER')
        self.assertEqual(len(self.f.read()['events']),1)

if __name__=='__main__':unittest.main()
