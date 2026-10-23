"""End-to-end scenarios: synthetic point cloud -> corridor analysis -> speech."""
import sys, unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from guidance import Guidance, corridor_candidates, fine_scan


def floor_points(obstacles=(),zmax=2.2):
    x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,zmax,.04))
    points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()]).astype(float)
    for obstacle in obstacles:
        points=np.vstack([points,np.array(obstacle,dtype=float)])
    return points,np.ones(len(points),bool),{'normal':[0,-1,0],'offset_m':1.}


def analysis(points,valid,plane,confirmed=True):
    return {'corridors':corridor_candidates(points,valid,plane,confirmed),
        'fine':fine_scan(points,valid,plane,confirmed),
        'motion_hint':{'state':'CAMERA_STILL'}}


def frame(index,guide):
    return {'state':'STREAMING','frame_age_ms':50,'session_id':'s','frame_id':f's:{index}',
        'native_depth':[{'level':1}]*3,'guidance_analysis':guide}


class GuidanceScenarioTests(unittest.TestCase):
    def walk(self,guide,frames=5):
        g=Guidance();g.set_mode('WALKING');result=None
        for index in range(1,frames+1):result=g.update(frame(index,guide))
        return result

    def test_open_floor_gives_forward_clear(self):
        points,valid,plane=floor_points()
        result=self.walk(analysis(points,valid,plane))
        self.assertIn('可走',result['speech']);self.assertIn('前方',result['speech'])

    def test_center_obstacle_suggests_slight_left(self):
        points,valid,plane=floor_points(obstacles=[[[0,.5,1.0],[.04,.5,1.0]]])
        result=self.walk(analysis(points,valid,plane))
        self.assertIn('左',result['speech']);self.assertEqual(result['traffic_decision'],'TURN')

    def test_missing_far_bands_stop_for_gap(self):
        points,valid,plane=floor_points(zmax=1.4)
        result=self.walk(analysis(points,valid,plane))
        self.assertIn('落差',result['speech'])

    def test_narrow_gap_between_obstacles(self):
        columns=[]
        for side in (-.3,.3):
            columns.extend([[side,.5,z] for z in (.6,.9,1.2,1.5)])
        points,valid,plane=floor_points(obstacles=[columns])
        result=self.walk(analysis(points,valid,plane))
        self.assertIn('窄',result['speech'])


if __name__=='__main__':unittest.main()
