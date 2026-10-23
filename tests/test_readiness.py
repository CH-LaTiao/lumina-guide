import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from readiness import assess


def ready_info():
    return {
        'state':'STREAMING','frame_age_ms':80,'aligned':True,'real_output':False,
        'geometry':{'ground_state':'REFERENCE_MATCHED_CANDIDATE',
            'body_calibration':{'state':'ACTIVE','required_passage_width_m':.8}},
        'guidance_analysis':{'direction_frame':'BODY','calibration_applied':True,
            'native_body_ambiguous':False},
        'guidance':{'required_passage_width_m':.8,'movement_allowed':False},
        'detection':{'status':'READY','provider':'CUDA'},
        'imu':{'state':'WALKING','accel_valid':True,'gyro_valid':True,'age_ms':20},
        'audio':{'route':{'available':True}},
        'online':{'configured':True},
    }


class ReadinessTests(unittest.TestCase):
    def test_all_software_gates_ready_but_movement_stays_locked(self):
        value=assess(ready_info())
        self.assertTrue(value['ready_for_body_guidance'])
        self.assertTrue(value['ready_for_auto_mode'])
        self.assertFalse(value['ready_for_movement'])
        self.assertEqual(value['stage'],'FIELD_VALIDATION')
        self.assertIn('移动许可门控尚未释放',value['blockers'])
        self.assertIn('受控场地真值和佩戴行走验收尚未完成',value['blockers'])
        self.assertEqual([x['id'] for x in value['next_actions']],['run_field_validation'])

    def test_missing_body_calibration_is_an_actionable_stage(self):
        info=ready_info();info['geometry']['body_calibration']={'state':'UNSET'}
        value=assess(info)
        self.assertFalse(value['ready_for_body_guidance'])
        self.assertEqual(value['stage'],'BODY_CALIBRATION')
        self.assertIn('身体安装标定未设置或已过期',value['blockers'])
        self.assertFalse(value['checks']['human_width']['ok'])
        action=next(x for x in value['next_actions'] if x['id']=='apply_body_calibration')
        self.assertEqual({f['key'] for f in action['fields']},
            {'camera_forward_m','camera_right_m','yaw_deg','required_passage_width_m'})
        self.assertTrue(all(f['value'] is None for f in action['fields']))

    def test_stale_or_cpu_detection_blocks_earlier_gate(self):
        info=ready_info();info['state']='FAULT';info['frame_age_ms']=None
        info['detection']={'status':'READY','provider':'CPU'}
        value=assess(info)
        self.assertEqual(value['stage'],'PERCEPTION')
        self.assertIn('相机画面不新鲜或未连接',value['blockers'])
        self.assertIn('YOLO未在CUDA READY状态',value['blockers'])
        self.assertEqual(value['next_actions'][0]['id'],'restore_perception')

    def test_freshness_limit_adapts_to_mode(self):
        info=ready_info();info['frame_age_ms']=450
        info['guidance']['mode']='STANDING'
        self.assertTrue(assess(info)['checks']['camera_fresh']['ok'])
        info['guidance']['mode']='WALKING'
        self.assertFalse(assess(info)['checks']['camera_fresh']['ok'])

    def test_camera_default_width_never_counts_as_measured_human_width(self):
        info=ready_info();info['geometry']['body_calibration']={'state':'ACTIVE'}
        value=assess(info)
        self.assertFalse(value['checks']['human_width']['ok'])
        self.assertIn('含白手杖人体净宽未设置',value['blockers'])

    def test_ground_reference_is_the_first_measurement_action(self):
        info=ready_info();info['geometry']={'ground_state':'UNKNOWN','body_calibration':{'state':'UNSET'}}
        value=assess(info)
        self.assertEqual(value['stage'],'GROUND_REFERENCE')
        action=value['next_actions'][0]
        self.assertEqual(action['id'],'apply_bench_reference')
        self.assertEqual({f['key'] for f in action['fields']},{'height_m','pitch_down_deg'})
        self.assertTrue(action['requires_user_measurement'])
        self.assertTrue(all(f['value'] is None for f in action['fields']))


if __name__=='__main__':unittest.main()
