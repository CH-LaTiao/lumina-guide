import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from status_console import line


class StatusConsoleTests(unittest.TestCase):
    def test_console_surfaces_stage_and_blockers(self):
        value=line({'state':'STREAMING','camera_fps':28.5,'frame_age_ms':120,
            'detection':{'provider':'CUDA','objects':[]},
            'guidance':{'mode':'WALKING','action':'STOP','corridors':[]},
            'readiness':{'stage':'BODY_CALIBRATION',
                'blockers':['身体安装标定未设置或已过期','移动许可门控尚未释放']},
            'online':{'state':'IDLE'},'audio':{'state':'IDLE'}})
        self.assertIn('就绪 BODY_CALIBRATION',value)
        self.assertIn('2项：身体安装标定未设置或已过期',value)


if __name__=='__main__':unittest.main()
