"""Safety regressions for a single decision stream shared by UI, voice and haptics."""
import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from guidance import Guidance
from feedback_sim import FeedbackSimulator
from live_view import State,advance_guidance

def frame(i=1,levels=(1,1,1),**extra):
    result={'state':'STREAMING','session_id':'s','frame_id':f's:{i}','frame_age_ms':50,
        'native_depth':[{'level':v} for v in levels], 'guidance_analysis':{}}
    result.update(extra);return result

class PipelineTests(unittest.TestCase):
    def test_walking_risk_rises_immediately_but_falls_after_three_distinct_frames(self):
        g=Guidance();g.set_mode('WALKING')
        r=g.update(frame(1,(1,3,1)));self.assertEqual(r['action'],'STOP')
        for _ in range(20):self.assertEqual(g.update(frame(2))['filtered_levels'],[1,3,1])
        self.assertEqual(g.update(frame(3))['filtered_levels'],[1,3,1])
        self.assertEqual(g.update(frame(4))['filtered_levels'],[1,1,1])
        self.assertEqual(g.update(frame(5,(1,3,1)))['action'],'STOP')
        r=g.update(frame(6,(1,0,1)));self.assertIn('UNKNOWN_DEPTH',r['reasons'])
    def test_lost_route_never_retains_old_go_speech(self):
        g=Guidance();g.set_mode('WALKING')
        analysis={'fine':[{'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.}]}
        for i in range(1,5):r=g.update(frame(i,guidance_analysis=analysis))
        self.assertTrue(r['can_continue'])
        r=g.update(frame(5));self.assertFalse(r['can_continue']);self.assertNotIn('可走',r['speech'])
        self.assertIsNone(r['candidate'])
    def test_stale_frame_never_retains_usable_candidate(self):
        g=Guidance();g.set_mode('WALKING')
        analysis={'fine':[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.}]}
        for i in range(1,5):g.update(frame(i,guidance_analysis=analysis))
        r=g.update(frame(4,frame_age_ms=201));self.assertFalse(r['fresh'])
        self.assertFalse(r['can_continue']);self.assertIsNone(r['candidate']);self.assertIn('感知不可用',r['speech'])
    def test_coordinate_change_does_not_keep_old_side_hazard(self):
        g=Guidance();g.set_mode('WALKING');g.update(frame(1,(3,1,1)))
        r=g.update(frame(2,guidance_analysis={'direction_frame':'BODY','native_body_levels':[1,1,3],
            'native_body_ambiguous':False,'body_transform':{'yaw_deg':20}}))
        self.assertEqual(r['filtered_levels'],[1,1,3]);self.assertIn('右侧',r['speech'])
    def test_unattended_loop_uses_detector_and_publishes_matching_event(self):
        with tempfile.TemporaryDirectory() as tmp,patch('live_view.time.monotonic',return_value=10):
            s=State();s.update(frame(),b'jpeg',{'captured':9.95})
            g=Guidance(clock=lambda:10.);g.set_mode('WALKING')
            feedback=FeedbackSimulator(Path(tmp)/'feedback.sqlite3',clock=lambda:10.)
            voice=Mock();detector=Mock()
            detector.read.return_value=({'status':'READY','age_ms':50,'frame_id':'s:1','objects':[
                {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]},None)
            r=advance_guidance(s,g,feedback,voice,detector)
            self.assertIn('CROSS_MODAL_HAZARD',r['reasons']);voice.follow.assert_called_once_with(r)
            for _ in range(10):
                status=s.read()[0]
                self.assertEqual(status['guidance']['event_id'],status['feedback']['speech']['guidance_event_id'])
                self.assertEqual(status['guidance']['speech'],status['feedback']['speech']['text'])
            self.assertEqual(len(feedback.events),1)
    def test_stalled_decision_worker_expires_even_with_new_camera_frames(self):
        with patch('live_view.time.monotonic',return_value=10):
            s=State();s.update(frame(),b'jpeg',{'captured':9.95})
            s.set_guidance({'mode':'WALKING','speech':'左侧可走。','candidate':'LEFT','fresh':True,
                'can_continue':True,'policy':{'fresh_limit_ms':200}},
                {'paused':False,'speech':{'text':'左侧可走。'},'haptics':[{'channel':0,'active':True}]})
        with patch('live_view.time.monotonic',return_value=11):
            s.update(frame(2),b'new',{'captured':10.95});status=s.read()[0]
            self.assertEqual(status['guidance']['traffic_decision'],'STOP')
            self.assertIsNone(status['guidance']['candidate']);self.assertFalse(status['guidance']['fresh'])
            self.assertFalse(any(r['active'] for r in status['feedback']['haptics']))
            self.assertIn('感知不可用',status['feedback']['speech']['text'])

if __name__=='__main__':unittest.main()
