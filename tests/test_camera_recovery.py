import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from camera_recovery import supervise_capture

class State:
    def __init__(self):self.info={};self.states=[]
    def update(self,value):self.info=value;self.states.append(value)
    def read(self):return self.info,None
class Stop:
    def __init__(self):self.done=False;self.waits=[]
    def is_set(self):return self.done
    def wait(self,seconds):self.waits.append(seconds)
class Detector:
    def __init__(self):self.invalidations=0
    def invalidate(self):self.invalidations+=1
class CameraRecoveryTests(unittest.TestCase):
    def test_retry_sequentially_clear_outputs_and_recover(self):
        state=State();stop=Stop();detector=Detector();calls=[];cancel=[]
        def capture(s,event,serial,d):
            calls.append(serial)
            if len(calls)<4:raise RuntimeError('unplugged')
            s.update({'state':'STREAMING','preview_frames':31});event.done=True
        supervise_capture(capture,state,stop,'camera',detector,lambda:cancel.append(1))
        self.assertEqual(stop.waits,[.5,1,2]);self.assertEqual(len(cancel),3)
        self.assertEqual(detector.invalidations,7);self.assertEqual(state.info['state'],'STOPPED')
    def test_backoff_capped_and_stoppable(self):
        state=State();stop=Stop()
        def fail(*_):
            if len(stop.waits)==8:stop.done=True
            raise RuntimeError('absent')
        supervise_capture(fail,state,stop,'camera')
        self.assertLessEqual(max(stop.waits),8)
if __name__=='__main__':unittest.main()
