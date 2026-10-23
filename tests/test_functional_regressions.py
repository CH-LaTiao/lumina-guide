import io,json,os,sys,tempfile,threading,time,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch,MagicMock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from box_depth import link
from online_query import OnlineQuery
from live_view import capture_shm,State
from voice_io import answer_text,pcm_wave

class FunctionalTests(unittest.TestCase):
    def test_question_routes_to_specific_templates(self):
        from scene_router import scene_for_question
        for text,scene in [('无障碍厕所在哪里','find_target'),('我要换乘2号线','metro'),('读一下上面的文字','read_sign'),
            ('前面有楼梯吗','stairs'),('路口红绿灯是什么颜色','traffic_signal'),('障碍物怎么绕开','obstacle'),('描述一下环境','general')]:
            self.assertEqual(scene_for_question(text),scene)
    def test_box_cross_session_missing_and_neighbor_rejected(self):
        a=[{'bbox':[0,0,10,10]}];b=[{'bbox':[0,0,10,10],'depth_z_p20_m':1}]
        for x,y in [('a:1','b:1'),(None,None),('a:1','a:2'),('bad','bad')]:
            self.assertEqual(link(a,b,x,y)[0]['depth_source'],'patch')
        self.assertEqual(link(a,b,'a:1','a:1')[0]['depth_source'],'cluster')
    def test_frozen_shm_times_out(self):
        tick=[10.];r=MagicMock();r.epoch=5
        r.intrinsics={'fx':1,'fy':1,'cx':1,'cy':1,'width':2,'height':2}
        r.latest.return_value=({'seq':1,'frame_id':1,'received_ns':10_000_000_000,'published_ns':10_000_000_000,
            'depth_ts_us':1,'color_ts_us':1,'height':2,'width':2,'depth_scale':1},b'\0'*8,b'jpeg')
        with patch('frame_shm.FrameShm',return_value=r),patch('native_depth.NativeDepth'),\
            patch('live_view.threading.Thread'),patch('live_view.cv2.imdecode',return_value=np.zeros((2,2,3),np.uint8)),\
            patch('live_view.time.monotonic',side_effect=lambda:tick[0]),\
            patch('live_view.time.sleep',side_effect=lambda dt:tick.__setitem__(0,tick[0]+dt)):
            with self.assertRaisesRegex(RuntimeError,'stopped advancing'):capture_shm(State(),threading.Event(),'serial')
        self.assertLess(tick[0],12.1);r.close.assert_called_once()
    def test_independent_deadline_cancel_and_late_worker(self):
        entered=threading.Event();release=threading.Event()
        def blocked(*a,**k):entered.set();release.wait(2);raise TimeoutError()
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'fake'}),\
            patch('online_query.ACTION_BUDGET_S',.08),patch('online_query.fetch_response',side_effect=blocked):
            q=OnlineQuery(Path(tmp)/'q.db')
            q.submit('test','general',{'captured':time.monotonic(),'jpeg':b'a','frame_id':'s:1'},True)
            self.assertTrue(entered.wait(.5));time.sleep(.14)
            self.assertEqual(q.read()['state'],'TIMEOUT');self.assertFalse(q.read()['busy'])
            release.set()
            end=time.monotonic()+1
            while q.workers and time.monotonic()<end:time.sleep(.01)
            self.assertEqual(q.workers,0);self.assertEqual(q.read()['state'],'TIMEOUT')
    def test_scene_change_does_not_inherit_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=OnlineQuery(Path(tmp)/'q.db');q._begin_round('metro');q.task['last_plan']={'fact':'old'}
            self.assertEqual(q._begin_round('stairs'),(1,None))
    def test_answer_speech_gate(self):
        result={'state':'CANDIDATE','plan':{'observations':[{'fact':'红色物体'}],'next_step':{'kind':'observe','say':'需要确认位置'}}}
        self.assertIn('红色物体',answer_text(result,'STANDING'))
        with self.assertRaises(ValueError):answer_text(result,'WALKING')
        result['plan']['next_step']={'kind':'move_candidate','say':'向左走'}
        with self.assertRaises(ValueError):answer_text(result,'STANDING')
    def test_recording_wav_format(self):
        import wave
        with wave.open(io.BytesIO(pcm_wave(b'\0'*32000))) as w:
            self.assertEqual((w.getframerate(),w.getnchannels(),w.getnframes()),(16000,1,16000))

if __name__=='__main__':unittest.main()
