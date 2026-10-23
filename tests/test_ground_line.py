import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ground_line import GroundLine


def info():
    return {'session_id':'s1','geometry':{'camera':{'width':640,'height':480,'sha256':'camera-a'}}}


class GroundLineTests(unittest.TestCase):
    def test_default_line_and_session_binding(self):
        line=GroundLine()
        default=line.read('s1','camera-a',480)
        self.assertEqual(default['state'],'DEFAULT')
        self.assertEqual(default['line_y'],264)
        value=line.set({'y':310},info())
        self.assertEqual(value['state'],'SET')
        self.assertEqual(value['line_y'],310)
        self.assertTrue(value['active'])
        invalid=line.read('s2','camera-a',480)
        self.assertEqual(invalid['state'],'INVALIDATED')
        self.assertFalse(invalid['active'])

    def test_clear_and_range_limits(self):
        line=GroundLine()
        with self.assertRaises(ValueError):
            line.set({'y':90},info())
        with self.assertRaises(ValueError):
            line.set({'y':470},info())
        with self.assertRaises(ValueError):
            line.set({'y':None},info())
        line.set({'y':300},info())
        cleared=line.clear(info())
        self.assertEqual(cleared['state'],'DEFAULT')
        self.assertFalse(cleared['active'])
