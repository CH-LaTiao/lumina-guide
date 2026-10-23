import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from guidance import Guidance,corridor_candidates,fine_scan,CameraMotion,standing_near_cue

def info(i=1,levels=(1,1,1),corridors=None):
    return {'state':'STREAMING','frame_age_ms':50,'session_id':'s','frame_id':f's:{i}',
        'native_depth':[{'level':n} for n in levels],
        'guidance_analysis':{'corridors':corridors or []}}

class GuidanceTests(unittest.TestCase):
    def test_auto_mode_uses_imu_gait_and_returns_to_standing(self):
        g=Guidance();f=info();f['imu']={'state':'WALKING','accel_valid':True,'gyro_valid':True}
        r=g.update(f)
        self.assertEqual(r['mode'],'WALKING');self.assertEqual(r['requested_mode'],'AUTO')
        self.assertEqual(r['mode_source'],'IMU_GAIT');self.assertEqual(r['policy']['output'],'SINGLE_ACTION')
        f2=info(2);f2['imu']={'state':'STANDING','accel_valid':True,'gyro_valid':True}
        r2=g.update(f2)
        self.assertEqual(r2['mode'],'STANDING');self.assertEqual(r2['requested_mode'],'AUTO')
        self.assertEqual(r2['policy']['output'],'FULL_SUMMARY')

    def test_manual_mode_is_not_overridden_by_imu(self):
        g=Guidance();g.set_mode('WALKING');f=info();f['imu']={'state':'STANDING','accel_valid':True,'gyro_valid':True}
        r=g.update(f)
        self.assertEqual(r['mode'],'WALKING');self.assertEqual(r['requested_mode'],'WALKING')
        self.assertEqual(r['mode_source'],'USER_SELECTED')

    def test_turning_pauses_old_route_confirmation(self):
        g=Guidance();g.set_mode('WALKING');f=info()
        f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE',
            'clear_width_m':.9}]
        f['imu']={'state':'WALKING','accel_valid':True,'gyro_valid':True,
            'metrics':{'turn_rate_dps':50}}
        for i in range(1,6):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['candidate']);self.assertEqual(r['action'],'OBSERVE')
        self.assertIn('TURN_IN_PROGRESS',r['reasons']);self.assertTrue(r['policy']['turn_in_progress'])

    def test_standing_silent_and_walking_short(self):
        g=Guidance();self.assertEqual(g.update(info())['speech'],'')
        g.set_mode('WALKING');r=g.update(info())
        self.assertLessEqual(len(r['speech']),25);self.assertFalse(r['movement_allowed'])
        self.assertIn('BODY_POSE_UNVERIFIED',r['reasons'])

    def test_walking_cues_lead_with_go_or_stop_and_stay_short(self):
        g=Guidance();g.set_mode('WALKING');f=info()
        f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE',
            'clear_width_m':.9}]
        for i in range(1,5):f['frame_id']=f's:{i}';go=g.update(f)
        self.assertEqual(go['speech'],'前方可走。')
        self.assertEqual(go['traffic_decision'],'GO')
        self.assertFalse(go['must_turn'])

        gap=info(levels=(1,1,1))
        gap['guidance_analysis']['fine']=[{'angle_deg':0,'state':'UNKNOWN','gap_m':1.4,
            'vertical_transition':'STEP_UP_OR_CURB'}]
        stop=Guidance();stop.set_mode('WALKING');result=stop.update(gap)
        self.assertEqual(result['speech'],'停，有台阶。')
        self.assertEqual(result['traffic_decision'],'STOP')
        self.assertFalse(result['can_continue'])
        self.assertLessEqual(max(len(go['speech']),len(result['speech'])),12)

    def test_standing_and_walking_handle_unknown_depth_differently(self):
        f=info(levels=(1,0,1));f['guidance_analysis']['fine']=[
            {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.9}]
        standing=Guidance().update(f)
        self.assertEqual(standing['action'],'UNKNOWN');self.assertNotIn('停下',standing['speech'])
        walking=Guidance();walking.set_mode('WALKING');result=walking.update(f)
        self.assertEqual(result['action'],'STOP');self.assertEqual(result['priority'],90)
        self.assertIn('深度不足',result['speech']);self.assertIsNone(result['candidate'])

    def test_standing_near_obstacles_are_combined_and_not_repeated(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);f=info(levels=(3,1,3))
        first=g.update(f);now[0]=8.;second=g.update(f)
        self.assertIn('两侧',first['speech']);self.assertNotIn('左侧',first['speech'])
        self.assertEqual(first['event_id'],second['event_id'])
        g.set_mode('WALKING');walking=g.update(f)
        self.assertEqual(walking['action'],'STOP');self.assertIn('停',walking['speech'])

    def test_standing_side_hazard_flicker_is_coalesced(self):
        now=[1.];g=Guidance(clock=lambda:now[0])
        left=info(levels=(3,1,1));first=g.update(left)
        now[0]=1.4;right=info(levels=(1,1,3));g.update(right)
        now[0]=1.7;third=g.update(right)
        now[0]=2.0;fourth=g.update(right)
        self.assertIn('左侧',first['speech'])
        self.assertIn('两侧',third['speech'])
        self.assertEqual(third['event_id'],fourth['event_id'])

    def test_standing_action_cue_ignores_unstable_labels(self):
        now=[1.];g=Guidance(clock=lambda:now[0])
        f=info(levels=(1,3,1))
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':1.0}]}
        first=g.update(f)
        f['detection']['objects'][0]['label']='person';now[0]=1.05
        second=g.update(f)
        now[0]=1.4;third=g.update(f)
        self.assertEqual(first['speech'],'前方近物，往左。')
        self.assertEqual(first['direction_hint'],'LEFT')
        self.assertEqual(second['speech'],first['speech'])
        self.assertEqual(second['event_id'],first['event_id'])
        self.assertEqual(third['speech'],first['speech'])
        self.assertEqual(third['event_id'],first['event_id'])

    def test_standing_near_cues_choose_a_short_action_direction(self):
        cases=(
            ((3,1,1),{0},'左侧近物，走中。','CENTER'),
            ((1,1,3),{2},'右侧近物，走中。','CENTER'),
            ((3,1,3),{0,2},'两侧近物，走中。','CENTER'),
            ((3,3,1),{0,1},'前方近物，往右。','RIGHT'),
            ((1,3,3),{1,2},'前方近物，往左。','LEFT'),
            ((3,3,3),{0,1,2},'三面近物，停。',None),
        )
        for levels,regions,text,direction in cases:
            speech,hint=standing_near_cue(list(levels),regions)
            self.assertEqual(speech,text);self.assertEqual(hint,direction);self.assertLessEqual(len(text),9)

    def test_walking_prefers_small_turn_over_unnecessarily_wide_side(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.6},
            {'angle_deg':20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.9}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'CENTER');self.assertIn('前方',r['speech'])
        self.assertEqual(r['traffic_decision'],'GO');self.assertTrue(r['can_continue'])
        self.assertFalse(r['must_turn'])
        self.assertEqual(r['confirmation_frames'],4)

    def test_confirmed_side_beats_unconfirmed_wider_centre(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'CLEAR_NO_GROUND_EVIDENCE','clear_width_m':0.9},
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.6}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'LEFT');self.assertEqual(r['direction_angle'],-20)
        self.assertNotIn('GROUND_UNCONFIRMED',r['reasons'])
    def test_urgent_no_debounce(self):
        g=Guidance();g.set_mode('WALKING');r=g.update(info(levels=(1,3,1)))
        self.assertEqual(r['action'],'STOP');self.assertEqual(r['priority'],100)

    def test_walking_side_hazard_detours_to_confirmed_centre(self):
        g=Guidance();g.set_mode('WALKING')
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
        f=info(levels=(3,1,1),corridors=corridors)
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'STOP_AND_DETOUR');self.assertEqual(r['priority'],99)
        self.assertEqual(r['candidate'],'CENTER')
        self.assertIn('改走前方',r['speech'])
        self.assertEqual(r['detour']['confirmation_frames'],4)
        self.assertIn('SIDE_HAZARD_DETOUR',r['reasons'])

    def test_walking_centre_hazard_detours_to_confirmed_side(self):
        g=Guidance();g.set_mode('WALKING')
        corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8},
            {'direction':'CENTER','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'RIGHT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.0}]
        f=info(levels=(1,3,1),corridors=corridors)
        f['guidance_analysis']['fine']=[{'angle_deg':20,'state':'OBSERVED_CLEAR_CANDIDATE',
            'clear_width_m':1.0}]
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'STOP_AND_DETOUR');self.assertEqual(r['candidate'],'RIGHT')
        self.assertIn('改走右侧',r['speech'])
        self.assertIn('NEAR_HAZARD_DETOUR',r['reasons'])

    def test_centre_hazard_detour_requires_full_confirmation(self):
        g=Guidance();g.set_mode('WALKING')
        corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
            {'direction':'CENTER','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
        f=info(levels=(1,3,1),corridors=corridors)
        for i in range(1,4):
            f['frame_id']=f's:{i}';r=g.update(f)
            self.assertEqual(r['action'],'STOP');self.assertIsNone(r['detour'])
        f['frame_id']='s:4';r=g.update(f)
        self.assertEqual(r['action'],'STOP_AND_DETOUR');self.assertIn('改走左侧',r['speech'])

    def test_walking_side_unknown_keeps_confirmed_centre_route(self):
        g=Guidance();g.set_mode('WALKING')
        f=info(levels=(0,1,1))
        f['guidance_analysis']['fine']=[
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2},
            {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}]
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'DIRECTION_CANDIDATE');self.assertEqual(r['candidate'],'CENTER')
        self.assertIn('前方可走',r['speech']);self.assertIn('UNKNOWN_DEPTH_ELSEWHERE',r['reasons'])

    def test_side_hazard_detour_requires_confirmed_ground_and_mapping(self):
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE','clear_width_m':.9},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
        f=info(levels=(3,1,1),corridors=corridors)
        g=Guidance();g.set_mode('WALKING')
        for i in range(1,6):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['detour']);self.assertNotEqual(r['action'],'STOP_AND_DETOUR')
        corridors[1]['state']='OBSERVED_CLEAR_CANDIDATE'
        f['guidance_analysis']['direction_frame']='BODY'
        g=Guidance();g.set_mode('WALKING')
        for i in range(1,6):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['detour']);self.assertNotEqual(r['action'],'STOP_AND_DETOUR')

    def test_body_mapped_side_hazard_can_detour_only_when_unambiguous(self):
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
        f=info(levels=(3,1,1),corridors=corridors)
        f['guidance_analysis'].update(direction_frame='BODY',native_body_levels=[3,1,1],
            native_body_ambiguous=False,required_passage_width_m=.9,native_region_mapping={'regions':[
                {'camera_region':'LEFT','body_direction':'LEFT','level':3},
                {'camera_region':'CENTER','body_direction':'CENTER','level':1},
                {'camera_region':'RIGHT','body_direction':'RIGHT','level':1}]})
        g=Guidance();g.set_mode('WALKING')
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'STOP_AND_DETOUR');self.assertIn('BODY_RELATIVE_DIRECTION',r['reasons'])
        f['guidance_analysis']['native_body_ambiguous']=True
        g=Guidance();g.set_mode('WALKING')
        for i in range(1,6):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['detour']);self.assertNotEqual(r['action'],'STOP_AND_DETOUR')

    def test_body_required_width_controls_detour(self):
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.85},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
        f=info(levels=(3,1,1),corridors=corridors)
        f['guidance_analysis'].update(direction_frame='BODY',native_body_levels=[3,1,1],
            native_body_ambiguous=False,required_passage_width_m=.9)
        g=Guidance();g.set_mode('WALKING')
        for i in range(1,6):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['detour']);self.assertNotEqual(r['action'],'STOP_AND_DETOUR')
        self.assertEqual(r['required_passage_width_m'],.9)

    def test_near_obstacle_uses_detected_label(self):
        g=Guidance();g.set_mode('WALKING')
        f=info(levels=(1,3,1))
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':1.1}]}
        r=g.update(f)
        self.assertIn('可能椅子',r['speech']);self.assertIn('OBJECT_LABEL_CANDIDATE',r['reasons'])

    def test_far_detection_does_not_name_object(self):
        g=Guidance();g.set_mode('WALKING')
        f=info(levels=(1,3,1))
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':2.4}]}
        r=g.update(f)
        self.assertIn('有障碍',r['speech']);self.assertNotIn('OBJECT_LABEL_CANDIDATE',r['reasons'])

    def test_near_obstacle_rejects_stale_or_cross_session_label(self):
        for age,frame_id in ((501,'s:1'),(80,'old:1')):
            g=Guidance();g.set_mode('WALKING');f=info(levels=(1,3,1))
            f['detection']={'status':'READY','age_ms':age,'frame_id':frame_id,'objects':[
                {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':1.1}]}
            r=g.update(f)
            self.assertIn('有障碍',r['speech']);self.assertNotIn('OBJECT_LABEL_CANDIDATE',r['reasons'])

    def test_fresh_centre_detection_stops_even_when_depth_regions_are_clear(self):
        f=info();f['frame_id']='s:4'
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
        g=Guidance();g.set_mode('WALKING');r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertEqual(r['priority'],98)
        self.assertIn('可能椅子',r['speech']);self.assertIn('CROSS_MODAL_HAZARD',r['reasons'])
        self.assertAlmostEqual(r['cross_modal_hazard']['distance_m'],.75)
        standing=Guidance().update(f)
        self.assertEqual(standing['action'],'NEAR_OBJECT');self.assertNotIn('停下',standing['speech'])

    def test_cross_modal_near_object_detours_to_confirmed_side(self):
        g=Guidance();g.set_mode('WALKING')
        detection={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
        corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
            {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.6}]
        guide={'corridors':corridors,'fine':[{'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE',
            'clear_width_m':.9}]}
        for i in range(1,5):
            f=info(i);f['frame_id']=f's:{i}';f['guidance_analysis']=guide;f['detection']=detection;r=g.update(f)
        self.assertEqual(r['action'],'STOP_AND_DETOUR');self.assertEqual(r['candidate'],'LEFT')
        self.assertIn('改走左侧',r['speech'])
        self.assertIn('CROSS_MODAL_HAZARD',r['reasons']);self.assertIn('NEAR_HAZARD_DETOUR',r['reasons'])

    def test_cross_modal_side_object_excludes_that_route(self):
        g=Guidance();g.set_mode('WALKING')
        f=info();f['guidance_analysis']['fine']=[
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.0},
            {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8}]
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'person','bbox':[60,260,140,430],'depth_patch_m':.75}]}
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'CENTER');self.assertIn('前方可走',r['speech'])
        self.assertIn('CROSS_MODAL_SIDE_HAZARD',r['reasons'])
        self.assertEqual(r['cross_modal_hazards']['LEFT']['direction'],'LEFT')

    def test_cross_modal_side_object_without_route_stops_specifically(self):
        g=Guidance();g.set_mode('WALKING');f=info()
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'person','bbox':[60,260,140,430],'depth_patch_m':.75}]}
        r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertEqual(r['priority'],95)
        self.assertIn('停，左侧可能行人',r['speech'])

    def test_cross_modal_centre_hazard_blocks_depth_side_detour(self):
        g=Guidance();g.set_mode('WALKING')
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}]
        guide={'corridors':corridors,'fine':[{'angle_deg':0,
            'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}]}
        detection={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
        for i in range(1,6):
            f=info(i);f['frame_id']=f's:{i}';f['guidance_analysis']=guide
            f['detection']=detection;r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertIsNone(r['detour'])
        self.assertIn('CROSS_MODAL_HAZARD',r['reasons'])
        self.assertEqual(r['cross_modal_hazard']['direction'],'CENTER')

    def test_stale_or_cross_session_detection_cannot_force_a_stop(self):
        for age,frame_id in ((251,'s:1'),(80,'other:1')):
            f=info();f['frame_id']='s:4'
            f['detection']={'status':'READY','age_ms':age,'frame_id':frame_id,'objects':[
                {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
            g=Guidance();g.set_mode('WALKING');r=g.update(f)
            self.assertNotIn('CROSS_MODAL_HAZARD',r['reasons']);self.assertIsNone(r['cross_modal_hazard'])

    def test_body_frame_detection_uses_body_centre_not_image_centre(self):
        transform={'yaw_deg':20,'camera_forward_m':.1,'camera_right_m':.05,'fx':400.,'cx':320.,'width':640}
        f=info();f['frame_id']='s:4';f['guidance_analysis'].update(direction_frame='BODY',body_transform=transform)
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
        g=Guidance();g.set_mode('WALKING');r=g.update(f)
        self.assertIsNone(r['cross_modal_hazard'])
        f['detection']['objects'][0]['bbox']=[150,300,198,380]
        r=g.update(f)
        self.assertIn('CROSS_MODAL_HAZARD',r['reasons'])
        self.assertIn('可能椅子',r['speech'])
        self.assertEqual(r['cross_modal_hazard']['direction_frame'],'BODY')

    def test_repeat_frame_does_not_confirm_direction(self):
        g=Guidance();g.set_mode('WALKING');f=info(corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE'}])
        for _ in range(20):r=g.update(f)
        self.assertIsNone(r['candidate'])
        for i in range(2,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'LEFT');self.assertIn('可走',r['speech'])
        self.assertFalse(r['movement_allowed']);self.assertIn('CAMERA_RELATIVE_DIRECTION',r['reasons'])

    def test_candidate_confirmation_expires_after_observation_gap(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        f=info(corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE'}])
        for i,stamp in enumerate((1.,1.2,1.4),start=1):
            now[0]=stamp;f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIsNone(r['candidate'])
        now[0]=2.5;f['frame_id']='s:4';r=g.update(f)
        self.assertIsNone(r['candidate']);self.assertEqual(r['confirmation_frames'],0)

    def test_walking_cautious_direction_without_ground(self):
        g=Guidance();g.set_mode('WALKING')
        f=info(corridors=[{'direction':'RIGHT','state':'CLEAR_NO_GROUND_EVIDENCE'}])
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'RIGHT')
        self.assertIn('右侧可走',r['speech']);self.assertIn('GROUND_UNCONFIRMED',r['reasons'])
        self.assertLessEqual(len(r['speech']),15)

    def test_body_frame_changes_direction_provenance(self):
        g=Guidance();g.set_mode('WALKING')
        f=info(corridors=[{'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE'}])
        f['guidance_analysis']['direction_frame']='BODY'
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['direction_frame'],'BODY')
        self.assertEqual(r['scope'],'BODY_MOUNT_CANDIDATE')
        self.assertIn('BODY_RELATIVE_DIRECTION',r['reasons'])
        self.assertNotIn('CAMERA_RELATIVE_DIRECTION',r['reasons'])
        self.assertNotIn('BODY_POSE_UNVERIFIED',r['reasons'])
        self.assertFalse(r['movement_allowed'])

    def test_walking_all_blocked_and_unknown(self):
        g=Guidance();g.set_mode('WALKING')
        blocked=[{'direction':d,'state':'BLOCKED_NEAR','nearest_obstacle_m':0.5} for d in ('LEFT','CENTER','RIGHT')]
        r=g.update(info(corridors=blocked));self.assertIn('有障碍',r['speech'])
        g.set_mode('WALKING');r=g.update(info());self.assertIn('方向未知',r['speech'])

    def test_walking_reports_far_obstacle_briefly(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':1.6}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'CENTER')
        self.assertEqual(r['speech'],'停，前方有物。');self.assertIn('OBSTACLE_AHEAD',r['reasons'])

    def test_far_fallback_uses_longest_obstacle_distance(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':1.3},
            {'angle_deg':20,'state':'BLOCKED_FAR','nearest_obstacle_m':2.0}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'RIGHT');self.assertEqual(r['speech'],'停，右侧有物。')

    def test_obstacle_height_class_and_speech(self):
        from guidance import fine_scan
        x,z=np.meshgrid(np.arange(-1,1,.04),np.arange(.6,2.2,.04))
        floor=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()])
        plane={'normal':[0,-1,0],'offset_m':1.}
        samples={
            'LOW':np.array([[0,.8,2.],[.04,.8,2.]]),
            'BODY':np.array([[0,.3,2.],[.04,.3,2.]]),
            'OVERHEAD':np.array([[0,-.8,2.],[.04,-.8,2.]])}
        for expected,obstacle in samples.items():
            points=np.vstack([floor,obstacle]);scan=fine_scan(points,np.ones(len(points),bool),plane,True)
            self.assertEqual(scan[3]['obstacle_class'],expected)
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':2.,
               'obstacle_class':'LOW','obstacle_height_range_m':[.1,.2]}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIn('低物',r['speech'])

    def test_walking_warns_when_obstacle_closes(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        def frame(i,distance):
            f=info(i);f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':distance}]
            return f
        for i in range(1,5):
            now[0]=1+i*.2;g.update(frame(i,2.0))
        now[0]+=0.2;g.update(frame(5,2.0))
        now[0]+=0.2;g.update(frame(6,1.6))
        now[0]+=0.2;r=g.update(frame(7,1.6))
        self.assertEqual(r['action'],'STOP');self.assertIn('靠近',r['speech'])
        self.assertIn('CLOSING_FAST',r['reasons'])

    def test_standing_does_not_track_closure(self):
        now=[1.];g=Guidance(clock=lambda:now[0])
        def frame(i,distance):
            f=info(i);f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':distance}]
            return f
        for i in range(1,5):
            now[0]=1+i*.2;g.update(frame(i,2.0))
        now[0]+=0.4;r=g.update(frame(5,1.6))
        self.assertNotIn('CLOSING_FAST',r['reasons'])

    def test_walking_uses_short_ttc_when_drop_is_below_rapid_threshold(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        def frame(i,distance):
            f=info(i);f['guidance_analysis']['fine']=[
                {'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':distance}]
            return f
        for i in range(1,5):
            now[0]=1+i*.2;g.update(frame(i,1.8))
        for i,distance in ((5,1.5),(6,1.45),(7,1.4)):
            now[0]=1+i*.2;r=g.update(frame(i,distance))
        self.assertEqual(r['action'],'STOP')
        self.assertIn('CLOSING_SHORT_TTC',r['reasons'])
        self.assertLessEqual(r['motion_risk']['ttc_to_clearance_s'],1.2)
        self.assertGreater(r['motion_risk']['closing_speed_m_s'],0)

    def test_distance_jitter_and_slow_change_do_not_trigger_closing(self):
        for distances in ((1.8,1.5,1.8,1.5),(1.8,1.78,1.76,1.74)):
            now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
            def frame(i,distance):
                f=info(i);f['guidance_analysis']['fine']=[
                    {'angle_deg':0,'state':'BLOCKED_FAR','nearest_obstacle_m':distance}]
                return f
            for i in range(1,5):
                now[0]=1+i*.2;g.update(frame(i,1.8))
            for offset,distance in enumerate(distances,start=5):
                now[0]=1+offset*.2;r=g.update(frame(offset,distance))
            self.assertNotIn('CLOSING_FAST',r['reasons'])
            self.assertIsNone(r['motion_risk'])

    def test_walking_stops_on_ground_gap(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'UNKNOWN','gap_m':1.4}]
        f=info();f['guidance_analysis']['fine']=fine
        r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertIn('落差',r['speech'])
        self.assertIn('GROUND_GAP',r['reasons'])

    def test_walking_distinguishes_step_up_from_down_edge(self):
        x,z=np.meshgrid(np.arange(-1,1,.04),np.arange(.6,1.0,.04))
        floor=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()])
        x,z=np.meshgrid(np.arange(-1,1,.04),np.arange(1.0,2.2,.04))
        raised=np.column_stack([x.ravel(),np.full(x.size,.88),z.ravel()])
        points=np.vstack([floor,raised]);valid=np.ones(len(points),bool)
        plane={'normal':[0,-1,0],'offset_m':1.}
        f=info();f['guidance_analysis']['fine']=fine_scan(points,valid,plane,True)
        g=Guidance();g.set_mode('WALKING');r=g.update(f)
        self.assertIn('台阶',r['speech']);self.assertIn('STEP_UP_OR_CURB',r['reasons'])
        f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'UNKNOWN','gap_m':1.4,
            'vertical_transition':'DOWN_STEP_OR_DROP'}]
        g=Guidance();g.set_mode('WALKING');r=g.update(f)
        self.assertIn('落差',r['speech']);self.assertIn('DOWN_STEP_OR_DROP',r['reasons'])

    def test_walking_offers_side_route_around_gap(self):
        g=Guidance();g.set_mode('WALKING')
        scan=[{'angle_deg':0,'state':'UNKNOWN','gap_m':1.4},
              {'angle_deg':-20,'state':'CLEAR_NO_GROUND_EVIDENCE'}]
        f=info();f['guidance_analysis']['fine']=scan
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'LEFT')
        self.assertIn('落差',r['speech']);self.assertIn('改走左侧',r['speech'])
        self.assertIn('GROUND_GAP',r['reasons'])

    def test_standing_supplies_full_direction_summary(self):
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':0.4},
            {'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'},
            {'direction':'RIGHT','state':'UNKNOWN'}]
        r=Guidance().update(info(corridors=corridors))
        self.assertEqual(r['speech'],'')
        self.assertIn('左侧0.4米有障碍',r['summary'])
        self.assertIn('前方未见障碍，地面未证实',r['summary'])
        self.assertIn('右侧未知',r['summary'])

    def test_standing_summary_lists_detected_objects(self):
        f=info()
        f['detection']={'objects':[
            {'label':'chair','bbox':[0,0,10,10],'cluster_distance_m':1.4},
            {'label':'tv','bbox':[0,0,10,10],'depth_patch_m':2.2},
            {'label':'unknown_thing','bbox':[0,0,10,10],'depth_patch_m':0.9}]}
        f['guidance_analysis']['corridors']=[{'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'}]
        r=Guidance().update(f)
        self.assertIn('检测到',r['summary']);self.assertIn('左侧椅子 1.4米',r['summary'])
        self.assertNotIn('unknown_thing',r['summary'])

    def test_standing_summary_uses_body_direction_for_objects(self):
        f=info()
        f['guidance_analysis']['corridors']=[{'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'}]
        f['guidance_analysis'].update(direction_frame='BODY',body_transform={
            'yaw_deg':0,'camera_forward_m':0,'camera_right_m':0,'fx':400.,'cx':320.,'width':640})
        f['detection']={'status':'READY','age_ms':80,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[100,260,140,430],'cluster_distance_m':1.0}]}
        summary=Guidance().update(f)['summary']
        self.assertIn('左侧椅子 1.0米',summary)

    def test_standing_summary_rejects_stale_or_cross_session_objects(self):
        f=info()
        f['guidance_analysis']['corridors']=[{'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'}]
        f['detection']={'status':'READY','age_ms':1500,'frame_id':'s:1','objects':[
            {'label':'chair','bbox':[0,0,10,10],'depth_patch_m':1.2}]}
        self.assertNotIn('椅子',Guidance().update(f)['summary'])
        f['detection'].update(age_ms=80,frame_id='other:1')
        self.assertNotIn('椅子',Guidance().update(f)['summary'])

    def test_walking_repeats_current_route_after_eight_seconds(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        f=info(corridors=[{'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}])
        for i in range(1,5):
            now[0]=1+i*.1;f['frame_id']=f's:{i}';r=g.update(f)
        event=r['event_id'];self.assertEqual(r['policy']['repeat_direction_s'],8)
        now[0]=9.2;f['frame_id']='s:5';self.assertEqual(g.update(f)['event_id'],event)
        now[0]=9.5;f['frame_id']='s:6';r=g.update(f)
        self.assertGreater(r['event_id'],event);self.assertEqual(r['speech'],'前方可走。')

    def test_walking_returns_to_centre_after_confirmed_side_detour(self):
        g=Guidance();g.set_mode('WALKING');f=info()
        f['guidance_analysis']['fine']=[
            {'angle_deg':0,'state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['candidate'],'LEFT')
        f['guidance_analysis']['fine']=[
            {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8},
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]
        for i in range(5,9):
            f['frame_id']=f's:{i}';r=g.update(f)
            if i<8:self.assertEqual(r['candidate'],'LEFT')
        self.assertEqual(r['candidate'],'CENTER')
        self.assertIn('前方可走',r['speech']);self.assertIn('CENTER_REACQUIRED',r['reasons'])
        self.assertEqual(r['policy']['center_recenter_min_width_m'],.6)

    def test_walking_does_not_recenter_into_narrow_or_unconfirmed_centre(self):
        for center in ({'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.4},
                {'angle_deg':0,'state':'CLEAR_NO_GROUND_EVIDENCE','clear_width_m':1.0}):
            g=Guidance();g.set_mode('WALKING');f=info()
            f['guidance_analysis']['fine']=[
                {'angle_deg':0,'state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
                {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]
            for i in range(1,5):f['frame_id']=f's:{i}';g.update(f)
            f['guidance_analysis']['fine']=[center,
                {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]
            for i in range(5,10):f['frame_id']=f's:{i}';r=g.update(f)
            self.assertEqual(r['candidate'],'LEFT')
    def test_stale_immediately_removes_candidate(self):
        g=Guidance();g.set_mode('WALKING');f=info()
        f['frame_age_ms']=201;r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertEqual(r['corridors'],[])

    def test_standing_tolerates_slow_refresh_but_walking_requires_200ms(self):
        f=info(corridors=[{'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'}])
        f['frame_age_ms']=450
        standing=Guidance().update(f)
        self.assertNotEqual(standing['action'],'UNAVAILABLE')
        self.assertIn('前方未见障碍',standing['summary'])
        walking=Guidance();walking.set_mode('WALKING');result=walking.update(f)
        self.assertEqual(result['action'],'STOP');self.assertIn('感知不可用',result['speech'])

    def test_silence_dedup_and_pause(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        a=g.update(info());b=g.update(info(2));self.assertEqual(a['event_id'],b['event_id'])
        now[0]=7.1;self.assertGreater(g.update(info(3))['event_id'],b['event_id'])
        g.set_mode('PAUSED');self.assertEqual(g.update(info())['speech'],'')
    def test_missing_floor_is_unknown(self):
        points=np.zeros((100,3));valid=np.zeros(100,bool)
        result=corridor_candidates(points,valid,None,False)
        self.assertTrue(all(c['state']=='UNKNOWN' for c in result))
    def test_supported_floor_and_obstacle(self):
        x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,2.2,.04))
        points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()]);valid=np.ones(len(points),bool)
        plane={'normal':[0,-1,0],'offset_m':1.}
        clear=corridor_candidates(points,valid,plane,True)[1]
        self.assertEqual(clear['state'],'OBSERVED_CLEAR_CANDIDATE')
        self.assertFalse(clear['body_movement_allowed'])
        points=np.vstack([points,[[0,.5,1.],[.04,.5,1.]]]);valid=np.ones(len(points),bool)
        self.assertEqual(corridor_candidates(points,valid,plane,True)[1]['state'],'BLOCKED_NEAR')
    def test_floor_hole_not_clear(self):
        x,z=np.meshgrid(np.arange(-1,1,.03),np.arange(.6,2.2,.03))
        points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()]);valid=points[:,2]<1.4
        result=corridor_candidates(points,valid,{'normal':[0,-1,0],'offset_m':1},True)[1]
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertEqual(result['gap_m'],1.4)

    def test_plane_without_reference_gives_cautious_state(self):
        x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,2.2,.04))
        points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()]);valid=np.ones(len(points),bool)
        plane={'normal':[0,-1,0],'offset_m':1.}
        result=corridor_candidates(points,valid,plane,False)[1]
        self.assertEqual(result['state'],'CLEAR_NO_GROUND_EVIDENCE')
        self.assertEqual(result['ground_cells'],0);self.assertGreaterEqual(result['coverage_cells'],8)

    def test_fine_scan_finds_clear_side_around_center_obstacle(self):
        from guidance import fine_scan
        x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,2.2,.04))
        points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()])
        points=np.vstack([points,[[0,.5,1.2],[.05,.5,1.2]]])
        valid=np.ones(len(points),bool);plane={'normal':[0,-1,0],'offset_m':1.}
        scan=fine_scan(points,valid,plane,True)
        states={c['angle_deg']:c['state'] for c in scan}
        self.assertEqual(states[0],'BLOCKED_FAR')
        self.assertEqual(states[-30],'OBSERVED_CLEAR_CANDIDATE')

    def test_clear_width_reflects_narrow_gap(self):
        from guidance import fine_scan
        x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,2.2,.04))
        points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()])
        for side in (-.3,.3):
            points=np.vstack([points,[[side,.5,zz] for zz in (.6,.9,1.2,1.5)]])
        valid=np.ones(len(points),bool);plane={'normal':[0,-1,0],'offset_m':1.}
        scan={c['angle_deg']:c for c in fine_scan(points,valid,plane,True)}
        self.assertEqual(scan[0]['state'],'OBSERVED_CLEAR_CANDIDATE')
        self.assertAlmostEqual(scan[0]['clear_width_m'],0.5)

    def test_walking_mentions_narrow_corridor(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.5}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIn('窄',r['speech']);self.assertIn('NARROW_CORRIDOR',r['reasons'])

    def test_walking_stops_when_corridor_too_narrow(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.2}]
        f=info();f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'STOP');self.assertIn('过窄',r['speech'])

    def test_walking_redirects_to_wider_side_when_narrow(self):
        g=Guidance();g.set_mode('WALKING')
        f=info();f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.2}]
        for i in range(1,5):f['frame_id']=f's:{i}';g.update(f)
        f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.2},
            {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.9}]
        f['frame_id']='s:5';r=g.update(f)
        self.assertIsNone(r['candidate']);self.assertIn('正在确认路线',r['speech'])
        for i in range(6,10):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['action'],'DIRECTION_CANDIDATE')
        self.assertEqual(r['traffic_decision'],'TURN')
        self.assertTrue(r['can_continue']);self.assertTrue(r['must_turn'])
        self.assertIn('改走左侧',r['speech'])

    def test_gap_takes_precedence_over_narrow_corridor(self):
        g=Guidance();g.set_mode('WALKING')
        scan=[{'angle_deg':0,'state':'UNKNOWN','gap_m':1.4},
              {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.2}]
        f=info();f['guidance_analysis']['fine']=scan
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertIn('落差',r['speech']);self.assertNotIn('可走',r['speech'])
        self.assertGreaterEqual(r['priority'],89)

    def test_walking_warns_when_corridor_narrows(self):
        now=[1.];g=Guidance(clock=lambda:now[0]);g.set_mode('WALKING')
        def frame(i,w):
            f=info(i);f['guidance_analysis']['fine']=[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':w}]
            return f
        for i in range(1,5):
            now[0]=1+i*.2;g.update(frame(i,0.9))
        now[0]+=0.2;g.update(frame(5,0.9))
        now[0]+=0.2;g.update(frame(6,0.6))
        now[0]+=0.2;r=g.update(frame(7,0.6))
        self.assertIn('变窄',r['speech']);self.assertIn('CORRIDOR_NARROWING',r['reasons'])

    def test_standing_summary_includes_clear_width(self):
        r=Guidance().update(info(corridors=[{'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':0.6}]))
        self.assertIn('净宽0.6米',r['summary'])

    def test_standing_summary_includes_obstacle_height_class(self):
        corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.7,'obstacle_class':'LOW'},
            {'direction':'CENTER','state':'BLOCKED_FAR','nearest_obstacle_m':1.8,'obstacle_class':'OVERHEAD'},
            {'direction':'RIGHT','state':'BLOCKED_FAR','nearest_obstacle_m':2.0,'obstacle_class':'BODY'}]
        r=Guidance().update(info(corridors=corridors))
        self.assertIn('左侧0.7米有低矮障碍',r['summary'])
        self.assertIn('前方1.8米上方有障碍',r['summary'])
        self.assertIn('右侧2.0米有障碍',r['summary'])

    def test_walking_uses_fine_angle_label(self):
        g=Guidance();g.set_mode('WALKING')
        fine=[{'angle_deg':-10,'state':'CLEAR_NO_GROUND_EVIDENCE'}]
        f=info();
        f['guidance_analysis']['fine']=fine
        for i in range(1,5):f['frame_id']=f's:{i}';r=g.update(f)
        self.assertEqual(r['direction_angle'],-10);self.assertIn('稍左',r['speech'])
    def test_camera_motion_is_not_gait(self):
        r=CameraMotion().update(np.zeros((480,640,3),np.uint8),1,'s')
        self.assertEqual(r['source'],'camera_flow_not_gait')

if __name__=='__main__':unittest.main()
