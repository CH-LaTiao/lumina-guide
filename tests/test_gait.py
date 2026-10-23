import json,math,random,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from gait import GaitDetector,load_profile,profile_from_samples


def sample(index,magnitude,gyro=.01):
    return {'accel_valid':True,'gyro_valid':True,'accel_ts_us':index*10000,'age_ms':1.,
        'accel':{'x':0.,'y':-magnitude,'z':0.},
        'gyro':{'x':gyro,'y':gyro,'z':gyro}}


class GaitTests(unittest.TestCase):
    def test_periodic_vertical_motion_becomes_walking(self):
        now=[0.];g=GaitDetector(clock=lambda:now[0])
        for i in range(400):
            now[0]=i*.01
            value=g.update(sample(i,9.8+.55*math.sin(2*math.pi*1.8*i*.01)))
        self.assertEqual(value['state'],'WALKING')
        self.assertGreaterEqual(value['metrics']['cadence_hz'],1.6)

    def test_still_sensor_becomes_standing(self):
        now=[0.];g=GaitDetector(clock=lambda:now[0]);random.seed(7)
        for i in range(400):
            now[0]=i*.01
            value=g.update(sample(i,9.8+random.uniform(-.02,.02)))
        self.assertEqual(value['state'],'STANDING')

    def test_irregular_motion_is_not_walking(self):
        now=[0.];g=GaitDetector(clock=lambda:now[0]);random.seed(9)
        for i in range(500):
            now[0]=i*.01
            value=g.update(sample(i,9.8+random.uniform(-.8,.8),gyro=.4))
        self.assertNotEqual(value['state'],'WALKING')

    def test_missing_accel_is_unknown(self):
        value=GaitDetector().update({'accel_valid':False})
        self.assertEqual(value['state'],'UNKNOWN')

    def test_gyro_along_gravity_is_reported_as_turn_rate(self):
        now=[0.];g=GaitDetector(clock=lambda:now[0]);value=None
        for i in range(20):
            now[0]=i*.01
            value=g.update(sample(i,9.8,gyro=1.0))
        self.assertLess(value['metrics']['turn_rate_dps'],-40)

    def test_profile_from_standing_and_walking_samples(self):
        still=[(i*.01,9.8+random.uniform(-.02,.02),.01) for i in range(300)]
        walk=[(i*.01,9.8+.55*math.sin(2*math.pi*1.8*i*.01),.08) for i in range(600)]
        profile=profile_from_samples(still,walk)
        self.assertLess(profile['thresholds']['still_std_max'],profile['thresholds']['walk_std_min'])
        self.assertTrue(1.2<=profile['thresholds']['cadence_min_hz']<profile['thresholds']['cadence_max_hz']<=3.5)

    def test_profile_roundtrip_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile.json'
            data={'schema':'lumina-gait-profile-1','thresholds':{
                'still_std_max':.08,'still_gyro_max':.12,'walk_std_min':.15,
                'cadence_min_hz':.9,'cadence_max_hz':2.8,'interval_cv_max':.35,'walk_gyro_max':4.}}
            path.write_text(json.dumps(data))
            self.assertEqual(load_profile(path)['walk_std_min'],.15)
            data['thresholds']['walk_std_min']=.01;path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):load_profile(path)

    def test_detector_hot_reloads_new_profile(self):
        now=[0.];g=None
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'profile.json';g=GaitDetector(clock=lambda:now[0],profile_path=path)
            g.update(sample(1,9.8));self.assertEqual(g.read()['profile_source'],'DEFAULT')
            data={'schema':'lumina-gait-profile-1','thresholds':{
                'still_std_max':.07,'still_gyro_max':.11,'walk_std_min':.2,
                'cadence_min_hz':1.,'cadence_max_hz':2.5,'interval_cv_max':.3,'walk_gyro_max':3.}}
            path.write_text(json.dumps(data))
            now[0]=2.;g.update(sample(2,9.8))
            value=g.read();self.assertEqual(value['profile_source'],'CALIBRATED')
            self.assertEqual(value['thresholds']['walk_std_min'],.2)

    def test_missing_profile_does_not_reset_each_second(self):
        now=[0.];g=None
        with tempfile.TemporaryDirectory() as tmp:
            g=GaitDetector(clock=lambda:now[0],profile_path=Path(tmp)/'missing.json')
            for i in range(400):
                now[0]=i*.01
                value=g.update(sample(i,9.8))
            self.assertEqual(value['state'],'STANDING')
            self.assertEqual(value['profile_source'],'DEFAULT')


if __name__=='__main__':unittest.main()
