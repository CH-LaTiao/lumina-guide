import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from detection_frames import DetectionFrames

class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.now=10;self.cache=DetectionFrames(clock=lambda:self.now)
    def test_previous_exact_frame_survives_new_result(self):
        self.cache.put('a:1',9.9,b'frame-one');self.cache.put('a:2',10,b'frame-two')
        self.now=10.2
        self.assertEqual(self.cache.get('a:1')[0],b'frame-one')
        self.assertEqual(self.cache.get('a:2')[0],b'frame-two')
        self.assertEqual(self.cache.get('nonexistent'),(None,None))
    def test_age_is_from_capture_and_expiry_drops_bytes(self):
        self.cache.put('a:1',9,b'image');self.now=11.01
        self.assertEqual(self.cache.get('a:1'),(None,None));self.assertEqual(self.cache.byte_count,0)
    def test_reconnect_clear_cannot_return_previous_session(self):
        self.cache.put('old:1',10,b'old');self.cache.clear();self.cache.put('new:1',10,b'new')
        self.assertIsNone(self.cache.get('old:1')[0]);self.assertEqual(self.cache.get('new:1')[0],b'new')
    def test_count_byte_limits_and_invalid_times(self):
        c=DetectionFrames(clock=lambda:self.now,max_frames=2,max_bytes=6)
        for i in range(3):c.put(str(i),10,b'abc')
        self.assertEqual(len(c.items),2);self.assertEqual(c.byte_count,6);self.assertIsNone(c.get('0')[0])
        c.put('large',10,b'1234567');c.put('future',11,b'x');c.put('nan',float('nan'),b'x')
        self.assertEqual(len(c.items),2)
        c.put('2',10,b'q');self.assertEqual(c.byte_count,4)
if __name__=='__main__':unittest.main()
