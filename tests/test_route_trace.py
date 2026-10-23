import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from route_trace import make_record, summarise


def status(frame='s:1',speech='前方可走，慢行。'):
    return {
        'state':'STREAMING','session_id':'s','frame_id':frame,'frame_age_ms':90,'camera_fps':28.5,
        'guidance_ms':6.2,'geometry':{'ground_state':'REFERENCE_MATCHED_CANDIDATE',
            'body_calibration':{'state':'ACTIVE'}},'detection':{'provider':'CUDA'},
        'software_version':'v9.9.9','software':{'commit':'abc123','source':'release',
            'code_root':'/release/v9.9.9'},
        'readiness':{'stage':'FIELD_VALIDATION','ready_for_movement':False},
        'guidance_analysis':{'motion_policy':'BODY_MOUNT_GROUND_TRACKING'},
        'imu':{'state':'WALKING','metrics':{'accel_mag_std':.3}},
        'guidance':{'direction_frame':'BODY','action':'DIRECTION_CANDIDATE','priority':75,
            'traffic_decision':'GO','can_continue':True,'must_turn':False,
            'requested_mode':'AUTO','mode_source':'IMU_GAIT',
            'speech':speech,'candidate':'CENTER','event_id':3,'movement_allowed':False,
            'required_passage_width_m':.8,'motion_risk':None,'detour':None,
            'reasons':['BODY_RELATIVE_DIRECTION'],
            'corridors':[{'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE',
                'nearest_obstacle_m':None,'clear_width_m':.9,'obstacle_class':None,
                'obstacle_height_range_m':None,'gap_m':None,'ground_cells':12,
                'required_ground_cells':12,'ignored_large_field':'dropped'}]},
    }


class RouteTraceTests(unittest.TestCase):
    def test_record_keeps_route_evidence_without_images(self):
        row=make_record(status(),'straight')
        self.assertEqual(row['direction_frame'],'BODY')
        self.assertEqual(row['candidate'],'CENTER')
        self.assertEqual(row['software_version'],'v9.9.9')
        self.assertEqual(row['software_commit'],'abc123')
        self.assertEqual(row['gait_state'],'WALKING')
        self.assertEqual(row['mode_source'],'IMU_GAIT')
        self.assertEqual(row['traffic_decision'],'GO')
        self.assertTrue(row['can_continue'])
        self.assertFalse(row['must_turn'])
        self.assertEqual(row['corridors'][0]['clear_width_m'],.9)
        self.assertNotIn('ignored_large_field',row['corridors'][0])
        self.assertNotIn('jpeg',row)
        self.assertFalse(row['movement_allowed'])

    def test_summary_counts_actions_risks_and_detours(self):
        a=make_record(status('s:1','前方可走，慢行。'),'straight')
        b=make_record(status('s:2','停下，前方有近障碍。'),'straight')
        b['motion_risk']={'ttc_to_clearance_s':.8};b['detour']={'direction':'CENTER'}
        b['cross_modal_hazards']={'LEFT':{'direction':'LEFT','distance_m':.75}}
        value=summarise([a,b],2.5)
        self.assertEqual(value['records'],2)
        self.assertEqual(value['motion_risks'],1)
        self.assertEqual(value['detours'],1)
        self.assertEqual(value['cross_modal_hazards'],1)
        self.assertEqual(value['speeches'],2)
        self.assertEqual(value['traffic_decisions'],{'GO':2})
        self.assertEqual(value['frames'],2)
        self.assertFalse(value['physical_output'])


if __name__=='__main__':unittest.main()
