import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
from visual_quality import center_range, rgb_quality

class VisualQualityTests(unittest.TestCase):
    def test_center_range_uses_mm_scale(self):
        depth=np.full((480,640),1000,np.uint16)
        self.assertAlmostEqual(center_range(depth,1.0),1.0)
    def test_center_range_rejects_empty_depth(self):
        self.assertIsNone(center_range(np.zeros((480,640),np.uint16),1.0))
    def test_overexposed_rgb(self):
        self.assertEqual(rgb_quality(np.full((480,640,3),255,np.uint8))['state'],'OVEREXPOSED')
    def test_dark_rgb(self):
        self.assertEqual(rgb_quality(np.zeros((480,640,3),np.uint8))['state'],'LOW_LIGHT')
    def test_normal_rgb(self):
        self.assertEqual(rgb_quality(np.full((480,640,3),128,np.uint8))['state'],'OK')

if __name__=='__main__':unittest.main()
