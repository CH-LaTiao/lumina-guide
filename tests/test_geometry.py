import math
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from body_calibration import BodyCalibration
from geometry import BenchReference, Geometry, canonical_hash, fit_plane, make_rays
from native_depth import NativeDepth


def model():
    value = {'width':640,'height':480,'fx':400.,'fy':400.,'cx':320.,'cy':240.,
             'distortion_model':'NONE','distortion':[0.]*8,'serial':'SYNTHETIC'}
    value['sha256'] = canonical_hash(value)
    return value


def floor_depth(height=.8):
    y = (np.arange(480)-240)/400
    depth = np.zeros((480,640),np.uint16)
    for row in range(241,480):
        z = height/y[row]
        if .3 <= z <= 4:
            depth[row] = round(z*1000)
    return depth


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.ref = BenchReference(lambda:self.now)
        self.model = model()
        self.engine = Geometry(self.model, NativeDepth(), self.ref)
        self.info = {'state':'STREAMING','frame_age_ms':30,'session_id':'session',
                     'geometry':{'state':'READY','camera':self.model}}

    def measured(self, height=.8):
        return self.ref.set({'measured':True,'stationary':True,'height_m':height,'pitch_down_deg':0}, self.info)

    def evaluate(self, raw, frame=1, age=10, session='session'):
        return self.engine.evaluate(raw, 1., age, f'{session}:{frame}', session)[0]

    def test_deprojection_axis_and_metric_scale(self):
        pixels, rays = make_rays(self.model)
        self.assertTrue(np.allclose(rays[:,0], (pixels[:,0]-320)/400))
        self.assertTrue(np.allclose(rays[:,1], (pixels[:,1]-240)/400))
        raw = np.zeros((480,640),np.uint16);raw[120:240,240:400]=1000
        obj = self.evaluate(raw)['objects'][0]
        self.assertAlmostEqual(obj['depth_z_p20_m'], 1., places=5)
        self.assertGreater(obj['camera_up_range_m'][0], 0)
        half = self.engine.evaluate(raw, .5, 10, 'session:2','session')[0]
        self.assertAlmostEqual(half['objects'][0]['depth_z_p20_m'], .5, places=5)

    def test_unsupported_distortion_and_bad_intrinsics_fail_closed(self):
        m=model();m['distortion_model']='KANNALA_BRANDT4'
        with self.assertRaises(ValueError):make_rays(m)
        m=model();m['fx']=0
        with self.assertRaises(ValueError):make_rays(m)
        m=model();m['distortion'][0]=math.nan
        with self.assertRaises(ValueError):make_rays(m)

    def test_plane_without_reference_never_removed_or_height_reported(self):
        value = self.evaluate(floor_depth())
        self.assertEqual(value['ground_state'],'PLANE_UNCONFIRMED')
        self.assertEqual(value['removed_plane_samples'],0)
        self.assertTrue(all(o['height_over_plane_m'] is None for o in value['objects']))
        self.assertFalse(value['movement_allowed'])

    def test_ground_needs_five_distinct_frames_and_measured_reference(self):
        self.measured();raw=floor_depth()
        for _ in range(8):value=self.evaluate(raw,1)
        self.assertEqual(value['stable_frames'],1)
        for frame in range(2,6):value=self.evaluate(raw,frame)
        self.assertEqual(value['ground_state'],'REFERENCE_MATCHED_CANDIDATE')
        self.assertGreater(value['removed_plane_samples'],100)
        self.assertEqual(value['objects'],[])

    def test_floor_and_suspended_object_preserve_cluster_height(self):
        self.measured();raw=floor_depth();raw[80:220,260:380]=1000
        for frame in range(1,6):value=self.evaluate(raw,frame)
        self.assertEqual(value['ground_state'],'REFERENCE_MATCHED_CANDIDATE')
        objects=value['objects'];self.assertEqual(len(objects),1)
        self.assertAlmostEqual(objects[0]['depth_z_p20_m'],1.,places=5)
        self.assertGreater(objects[0]['height_over_plane_m'][0],.8)

    def test_table_does_not_match_floor_height(self):
        self.measured(1.5)
        for frame in range(1,6):value=self.evaluate(floor_depth(.8),frame)
        self.assertEqual(value['ground_state'],'UNKNOWN')
        self.assertEqual(value['removed_plane_samples'],0)

    def test_wall_and_line_do_not_pass_floor_fit(self):
        x,z=np.meshgrid(np.linspace(-1,1,30),np.linspace(.5,3,30))
        wall=np.column_stack((x.ravel(),z.ravel(),np.ones(x.size)))
        self.assertIsNone(fit_plane(wall,[0,-1,0],.8))
        line=np.column_stack((np.arange(100)*.01,np.full(100,.8),np.ones(100)))
        self.assertIsNone(fit_plane(line,[0,-1,0],.8))

    def test_measured_pitch_accepts_matching_plane_rejects_wrong_vertical(self):
        x,z=np.meshgrid(np.linspace(-1,1,25),np.linspace(.5,3,25))
        n=[0,-math.cos(math.pi/6),-math.sin(math.pi/6)]
        y=(.8+z*n[2])/-n[1]
        points=np.column_stack((x.ravel(),y.ravel(),z.ravel()))
        self.assertIsNotNone(fit_plane(points,n,.8))
        self.assertIsNone(fit_plane(points,[0,-1,0],.8))

    def test_reordered_and_wrong_session_frames_do_not_accumulate_stability(self):
        self.measured();raw=floor_depth()
        self.evaluate(raw,5)
        self.assertEqual(self.evaluate(raw,4)['state'],'DUPLICATE_OR_REORDERED')
        bad=self.engine.evaluate(raw,1,10,'other:99','session')[0]
        self.assertEqual(bad['state'],'INVALID_FRAME_ID')
        value=self.evaluate(raw,6)
        self.assertEqual(value['stable_frames'],2)

    def test_stale_invalid_depth_and_reference_lease(self):
        self.measured();raw=floor_depth()
        value=self.evaluate(raw,age=201)
        self.assertEqual(value['state'],'INVALID_OR_STALE');self.assertEqual(value['objects'],[])
        self.assertEqual(self.evaluate(np.zeros_like(raw))['ground_state'],'UNKNOWN')
        self.now=130.
        self.assertIsNone(self.ref.read('session',self.model['sha256']))
        self.assertIsNone(self.evaluate(raw)['reference'])

    def test_reconnect_or_intrinsic_change_invalidates_reference(self):
        self.measured()
        self.assertIsNone(self.ref.read('new',self.model['sha256']))
        self.measured()
        self.assertIsNone(self.ref.read('session','changed'))

    def test_input_constraints_and_explicit_measurement(self):
        for h in (None,True,0,3,math.nan,math.inf):
            with self.assertRaises(ValueError):
                self.ref.set({'height_m':h,'pitch_down_deg':0,'measured':True,'stationary':True},self.info)
        with self.assertRaises(ValueError):self.ref.set({'height_m':.8,'pitch_down_deg':0},self.info)
        self.measured();self.ref.set({'clear':True},self.info)
        self.assertIsNone(self.ref.read('session',self.model['sha256']))

    def test_low_coverage_retains_near_evidence_and_unknown_near_count(self):
        raw=np.zeros((480,640),np.uint16);raw[80:180,240:400]=500;raw[400:480]=100
        value=self.evaluate(raw)
        self.assertEqual(value['ground_state'],'UNKNOWN')
        self.assertGreater(value['counts']['unknown_near'],0)
        self.assertEqual(value['objects'][0]['level'],'URGENT_CANDIDATE')
        self.assertEqual(value['removed_plane_samples'],0)

    def test_manual_ground_line_restricts_ground_region(self):
        self.measured();raw=floor_depth()
        default=self.engine.evaluate(raw,1.,10,'session:1','session')[0]
        custom=self.engine.evaluate(raw,1.,10,'session:2','session',ground_line_y=300)[0]
        self.assertEqual(custom['ground_line']['line_y'],300)
        self.assertGreater(custom['ground_line']['ground_samples'],0)
        self.assertLess(custom['ground_line']['ground_samples'],
            default['ground_line']['ground_samples'])

    def test_body_mount_reference_tracks_ground_without_static_bench(self):
        body=BodyCalibration(lambda:self.now)
        info={'state':'STREAMING','frame_age_ms':30,'session_id':'session',
              'geometry':{'state':'READY','camera':self.model,
                  'ground_state':'REFERENCE_MATCHED_CANDIDATE',
                  'reference':{'sha256':'initial-reference','height_m':.8,
                               'pitch_down_deg':0,'scope':'STATIONARY_BENCH_ONLY'}}}
        body.set({'measured':True,'stationary':True,'camera_forward_m':0,
                  'camera_right_m':0,'yaw_deg':0,'required_passage_width_m':.7},info)
        engine=Geometry(self.model,NativeDepth(),self.ref,body)
        raw=floor_depth()
        for frame in range(1,6):
            value=engine.evaluate(raw,1.,10,f'session:{frame}','session')[0]
        self.assertEqual(value['ground_state'],'REFERENCE_MATCHED_CANDIDATE')
        self.assertEqual(value['reference']['source'],'BODY_MOUNT')

if __name__=='__main__':unittest.main()
