"""Perception must remain fresh independently of preview encoding or display."""
import sys,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from live_view import State,Latest,render_loop,preview_loop,advance_guidance,guidance_loop
from guidance import Guidance
from feedback_sim import FeedbackSimulator
import tempfile


def sample(index,stamp):
    return {'state':'STREAMING','session_id':'s','frame_id':f's:{index}',
        'native_depth':[{'level':1} for _ in range(3)],'guidance_analysis':{}}


class PublicationTests(unittest.TestCase):
    def test_background_guidance_observes_published_frames_and_revokes_stopped_perception(self):
        state=State();stop=threading.Event();voice=Mock();observed=threading.Event()
        voice.follow.side_effect=lambda result,**kw:observed.set() if result.get('frame_id')=='s:2' else None
        with tempfile.TemporaryDirectory() as tmp:
            guide=Guidance();guide.set_mode('WALKING')
            feedback=FeedbackSimulator(Path(tmp)/'feedback.sqlite3')
            worker=threading.Thread(target=guidance_loop,args=(state,guide,feedback,voice,None,stop));worker.start()
            try:
                stamp=time.monotonic();state.publish_perception(sample(2,stamp),{'captured':stamp,'frame_id':'s:2'})
                self.assertTrue(observed.wait(1));self.assertTrue(state.read()[0]['guidance']['fresh'])
                time.sleep(.21)
                self.assertFalse(state.read()[0]['guidance']['fresh'])
            finally:stop.set();state.perception_event.set();worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_preview_keeps_its_original_frame_and_time_as_perception_advances(self):
        state=State()
        with patch('live_view.time.monotonic',return_value=10.1):
            state.publish_perception(sample(1,10),{'captured':10.,'frame_id':'s:1'})
            self.assertTrue(state.publish_preview('s:1','s',10.,b'first'))
            state.publish_perception(sample(2,10.09),{'captured':10.09,'frame_id':'s:2'})
            info,jpeg=state.read()
        self.assertEqual(info['frame_id'],'s:2');self.assertEqual(info['frame_age_ms'],10)
        self.assertEqual(info['preview_frame_id'],'s:1');self.assertEqual(info['preview_age_ms'],100)
        self.assertEqual(jpeg,b'first');self.assertEqual(state.peek_jpeg()[1],10.)

    def test_late_preview_cannot_refresh_expired_perception(self):
        state=State()
        with patch('live_view.time.monotonic',return_value=10):
            state.publish_perception(sample(1,9.99),{'captured':9.99,'frame_id':'s:1'})
        with patch('live_view.time.monotonic',return_value=11):
            state.publish_preview('s:1','s',9.99,b'late')
            info,jpeg=state.read()
        self.assertEqual(info['state'],'STALE');self.assertIsNone(jpeg)
        self.assertEqual(info['frame_age_ms'],1010)

    def test_fresh_perception_does_not_retag_a_stale_image(self):
        state=State()
        with patch('live_view.time.monotonic',return_value=10):
            state.publish_perception(sample(1,10),{'captured':10.,'frame_id':'s:1'})
            state.publish_preview('s:1','s',10.,b'old')
        with patch('live_view.time.monotonic',return_value=11):
            state.publish_perception(sample(2,10.99),{'captured':10.99,'frame_id':'s:2'})
            info,jpeg=state.read()
        self.assertEqual(info['state'],'STREAMING');self.assertEqual(info['preview_state'],'STALE')
        self.assertEqual(info['preview_frame_id'],'s:1');self.assertIsNone(jpeg)

    def test_preview_from_old_session_or_reordered_frame_is_rejected(self):
        state=State()
        state.publish_perception(sample(2,10),{'captured':10.,'frame_id':'s:2'})
        self.assertTrue(state.publish_preview('s:2','s',10.,b'good'))
        self.assertFalse(state.publish_preview('s:1','s',9.9,b'old'))
        state.publish_perception(dict(sample(1,11),session_id='new',frame_id='new:1'),{'captured':11.,'frame_id':'new:1'})
        self.assertFalse(state.publish_preview('s:3','s',10.9,b'old-session'))
        self.assertIsNone(state.peek_jpeg()[0])
        self.assertFalse(state.publish_preview('new:2','new',12.,b'not-yet-perceived'))

    def test_decision_source_expires_at_200ms_even_when_perception_continues(self):
        state=State()
        with patch('live_view.time.monotonic',return_value=10):
            state.publish_perception(sample(1,9.95),{'captured':9.95,'frame_id':'s:1'})
            state.set_guidance({'mode':'WALKING','speech':'左侧可走。','fresh':True,'candidate':'LEFT',
                'frame_id':'s:1','policy':{'fresh_limit_ms':200}})
        with patch('live_view.time.monotonic',return_value=10.16):
            state.publish_perception(sample(2,10.15),{'captured':10.15,'frame_id':'s:2'})
            info,_=state.read()
        self.assertFalse(info['guidance']['fresh']);self.assertIsNone(info['guidance']['candidate'])
        self.assertEqual(info['guidance']['traffic_decision'],'STOP')

    def test_preview_failure_never_faults_the_perception_stream(self):
        state=State();stamp=time.monotonic();state.publish_perception(sample(1,stamp),{'captured':stamp})
        queue=Latest();queue.put({'item':{'frame_id':'s:1','captured':stamp}});stop=threading.Event()
        def fail(_):stop.set();raise RuntimeError('JPEG failure')
        with patch('live_view.build_preview',side_effect=fail):preview_loop(queue,state,stop,'s')
        info,_=state.read();self.assertEqual(info['state'],'STREAMING')
        self.assertIn('JPEG failure',info['preview_error'])

    def test_blocked_jpeg_does_not_block_real_perception_loop_or_guidance(self):
        draw=Latest();state=State();stop=threading.Event();entered=threading.Event();release=threading.Event()
        engine=Mock();engine.evaluate.side_effect=lambda *args,**kwargs:({'state':'READY','ground_state':'UNKNOWN','objects':[]},np.zeros(1,bool))
        analysis=Mock();analysis.evaluate.return_value={'motion_hint':{'state':'CAMERA_STILL'},'corridors':[],'fine':[]}
        body=Mock();body.read.return_value=None
        def blocked(_):
            entered.set();release.wait(3);return b'jpeg',{},{}
        with patch('native_depth.NativeDepth'),patch('geometry.Geometry',return_value=engine), \
                patch('guidance.SceneAnalysis',return_value=analysis),patch('live_view.build_preview',side_effect=blocked):
            worker=threading.Thread(target=render_loop,args=(draw,state,stop,Mock(),body,'serial','camera',{},'s'))
            worker.start()
            try:
                for index in range(1,13):
                    if index==2:
                        self.assertTrue(entered.wait(1));time.sleep(.23)
                    stamp=time.monotonic()
                    item={'captured':stamp,'rgb':np.zeros((4,4,3),np.uint8),'raw':np.ones((4,4),np.uint16),
                        'scale':1.,'frame_id':f's:{index}','model':{'sha256':'m','height':4},
                        'hazards':[{'level':1,'coverage':1.,'distance_p10_m':2.} for _ in range(3)],
                        'count':index,'camera_fps':30.,'rate':30.,'align_ms':1.,'native_ms':1.,'depth_us':index,'color_us':index}
                    draw.put(item);deadline=time.monotonic()+1
                    while state.read()[0].get('frame_id')!=f's:{index}' and time.monotonic()<deadline:time.sleep(.002)
                    self.assertEqual(state.read()[0].get('frame_id'),f's:{index}')
                self.assertFalse(release.is_set());self.assertIsNone(state.read()[1])
                with tempfile.TemporaryDirectory() as tmp:
                    guide=Guidance();guide.set_mode('WALKING')
                    feedback=FeedbackSimulator(Path(tmp)/'feedback.sqlite3')
                    result=advance_guidance(state,guide,feedback,Mock())
                    self.assertTrue(result['fresh']);self.assertEqual(result['frame_id'],'s:12')
                time.sleep(.21)
                self.assertFalse(state.read()[0]['guidance']['fresh'])
            finally:stop.set();release.set();worker.join(2)
            self.assertFalse(worker.is_alive())


if __name__=='__main__':unittest.main()
