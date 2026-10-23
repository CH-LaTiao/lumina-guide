import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from detection import prepare,decode,patch_depth

class DetectionTests(unittest.TestCase):
    def test_reconnect_discards_inflight_result(self):
        import tempfile,threading,time,types
        from unittest.mock import patch
        from detection import Detector
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        class Session:
            def get_providers(self):return ['CPUExecutionProvider']
            def get_modelmeta(self):return types.SimpleNamespace(custom_metadata_map={'names':"{0:'person'}"})
            def run(self,*args):
                entered.set();release.wait(2);finished.set();return [np.zeros((1,84,8400),np.float32)]
        ort=types.SimpleNamespace(SessionOptions=types.SimpleNamespace,get_available_providers=lambda:['CPUExecutionProvider'],InferenceSession=lambda *a,**k:Session())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'onnxruntime':ort}):
            model=Path(tmp)/'m.onnx';model.write_bytes(b'fake-model-for-unit-test')
            d=Detector(model)
            try:
                d.submit(np.zeros((480,640,3),np.uint8),np.zeros((480,640),np.uint16),1,'old:1',time.monotonic())
                self.assertTrue(entered.wait(2));d.invalidate();release.set()
                self.assertTrue(finished.wait(2));d.stop.set();d.thread.join(2)
                self.assertIsNone(d.read()[1]);self.assertEqual(d.generation,1)
            finally:release.set();d.stop.set();d.thread.join(2)
    def test_letterbox_and_inverse(self):
        tensor,t=prepare(np.zeros((480,640,3),np.uint8));self.assertEqual(tensor.shape,(1,3,640,640));self.assertEqual(t,(1,0,80,640,480))
        output=np.zeros((1,84,8400),np.float32);output[0,:4,0]=[320,320,100,100];output[0,4,0]=.9
        boxes=decode(output,t,{0:'person'});self.assertEqual(boxes[0]['bbox'],[270,190,370,290])
    def test_nms_same_class(self):
        output=np.zeros((1,84,8400),np.float32)
        for i in (0,1):output[0,:4,i]=[320,320,100,100];output[0,4,i]=.9
        self.assertEqual(len(decode(output,(1,0,80,640,480),{0:'person'})),1)
    def test_missing_depth(self):
        result=patch_depth(np.zeros((480,640),np.uint16),[10,10,100,100],1)
        self.assertIsNone(result['depth_patch_m'])
    def test_patch_scale(self):
        result=patch_depth(np.full((480,640),1000,np.uint16),[10,10,100,100],.5)
        self.assertEqual(result['depth_patch_m'],.5)
    def test_nan_rejected(self):
        out=np.zeros((1,84,8400),np.float32);out[0,0,0]=np.nan
        with self.assertRaises(ValueError):decode(out,(1,0,80,640,480),{})
if __name__=='__main__':unittest.main()
