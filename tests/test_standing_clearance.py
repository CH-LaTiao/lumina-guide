"""Near-wall adjustments compare measured space instead of always saying center."""
import math,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from guidance import Guidance


def frame(index=1,distances=(2.98,1.20,.68),levels=(1,2,3)):
    return {'state':'STREAMING','frame_age_ms':50,'session_id':'s','frame_id':f's:{index}',
        'native_depth':[{'region':i,'level':level,'distance_p10_m':distance,'coverage':.9}
            for i,(level,distance) in enumerate(zip(levels,distances))],
        'guidance_analysis':{'direction_frame':'CAMERA','corridors':[]}}


class StandingClearanceTests(unittest.TestCase):
    def test_center_near_evidence_never_recommends_center_across_side_combinations(self):
        for left in range(4):
            for right in range(4):
                for center in (1.05,1.2,1.49,1.5,2.8):
                    with self.subTest(left=left,right=right,center=center):
                        distances=({0:None,1:3.,2:1.6,3:.7}[left],center,
                            {0:None,1:3.,2:1.6,3:.7}[right])
                        result=Guidance().update(frame(distances=distances,levels=(left,2,right)))
                        self.assertNotEqual(result['direction_hint'],'CENTER')

    def test_second_screenshot_center_below_candidate_minimum_never_falls_back_to_center(self):
        result=Guidance().update(frame(distances=(1.56,1.05,.7),levels=(2,2,3)))
        self.assertEqual(result['direction_hint'],'LEFT')
        self.assertNotIn('走中',result['speech'])

    def test_both_sides_close_and_center_near_require_stop(self):
        result=Guidance().update(frame(distances=(.806,1.444,.802),levels=(3,2,3)))
        self.assertIsNone(result['direction_hint']);self.assertIn('停',result['speech'])

    def test_every_sector_below_minimum_has_no_direction(self):
        result=Guidance().update(frame(distances=(1.1,1.05,1.2),levels=(2,2,2)))
        self.assertIsNone(result['direction_hint']);self.assertIn('停',result['speech'])

    def test_center_near_support_cannot_be_ignored_just_because_p10_is_far(self):
        result=Guidance().update(frame(distances=(2.5,3.0,.7),levels=(1,2,3)))
        self.assertNotEqual(result['direction_hint'],'CENTER')

    def test_near_center_withdraws_old_center_hint_without_stabilization_delay(self):
        now=[10.];guide=Guidance(clock=lambda:now[0])
        first=guide.update(frame(distances=(.6,3.2,.6),levels=(3,1,3)))
        self.assertEqual(first['direction_hint'],'CENTER')
        now[0]+=.05
        result=guide.update(frame(2,(.6,1.05,.6),(3,2,3)))
        self.assertIsNone(result['direction_hint']);self.assertIn('停',result['speech'])

    def test_left_supported_urgent_pixels_prevent_left_even_if_p10_seems_far(self):
        result=Guidance().update(frame(distances=(1.56,1.05,.7),levels=(3,2,3)))
        self.assertIsNone(result['direction_hint'])

    def test_mirrored_second_screenshot_adjusts_right(self):
        result=Guidance().update(frame(distances=(.7,1.05,1.56),levels=(3,2,2)))
        self.assertEqual(result['direction_hint'],'RIGHT');self.assertNotIn('走中',result['speech'])

    def test_user_corridor_example_prefers_left_of_near_right_wall(self):
        result=Guidance().update(frame())
        self.assertEqual(result['speech'],'前方近物，偏左。')
        self.assertEqual(result['direction_hint'],'LEFT')
        self.assertEqual(result['standing_clearances_m'],[2.98,1.2,.68])
        self.assertFalse(result['movement_allowed']);self.assertFalse(result['can_continue'])
        self.assertIsNone(result['candidate'])

    def test_mirrored_corridor_prefers_right(self):
        result=Guidance().update(frame(distances=(.68,1.2,2.98),levels=(3,2,1)))
        self.assertEqual(result['speech'],'前方近物，偏右。');self.assertEqual(result['direction_hint'],'RIGHT')

    def test_two_clear_levels_still_compare_actual_distance(self):
        result=Guidance().update(frame(distances=(3.0,2.1,.68),levels=(1,1,3)))
        self.assertEqual(result['direction_hint'],'LEFT')

    def test_center_stays_preferred_when_it_has_more_space(self):
        result=Guidance().update(frame(distances=(2.5,3.2,.68),levels=(1,1,3)))
        self.assertEqual(result['direction_hint'],'CENTER')

    def test_small_advantage_does_not_cause_lateral_switch(self):
        result=Guidance().update(frame(distances=(2.6,2.4,.68),levels=(1,1,3)))
        self.assertEqual(result['direction_hint'],'CENTER')

    def test_missing_central_measurement_does_not_invent_a_comparison(self):
        result=Guidance().update(frame(distances=(3.0,None,.68),levels=(1,1,3)))
        self.assertNotEqual(result['direction_hint'],'LEFT')

    def test_invalid_distance_or_low_coverage_does_not_support_lateral_choice(self):
        for value in (None,math.nan,math.inf,True,-1,'3.0'):
            with self.subTest(value=value):
                result=Guidance().update(frame(distances=(value,1.2,.68)))
                self.assertNotEqual(result['direction_hint'],'LEFT')
        value=frame();value['native_depth'][0]['coverage']=.4
        self.assertNotEqual(Guidance().update(value)['direction_hint'],'LEFT')

    def test_unknown_center_never_receives_center_action(self):
        result=Guidance().update(frame(distances=(3.,None,.68),levels=(1,0,3)))
        self.assertIsNone(result['direction_hint']);self.assertIn('停',result['speech'])

    def test_detector_hazard_on_left_prevents_left_adjustment(self):
        value=frame();value['detection']={'status':'READY','age_ms':50,'frame_id':'s:1',
            'objects':[{'label':'person','bbox':[20,100,180,400],'depth_patch_m':.8}]}
        self.assertNotEqual(Guidance().update(value)['direction_hint'],'LEFT')

    def test_stabilization_does_not_hold_old_hint_into_a_new_hazard(self):
        now=[10.];guide=Guidance(clock=lambda:now[0])
        self.assertEqual(guide.update(frame())['direction_hint'],'LEFT')
        now[0]+=.05
        result=guide.update(frame(2,(.6,2.4,3.),(3,1,1)))
        self.assertNotEqual(result['direction_hint'],'LEFT')

    def test_camera_distances_are_not_misused_when_body_mapping_is_ambiguous(self):
        value=frame();value['guidance_analysis'].update(direction_frame='BODY',
            native_body_levels=[1,2,3],native_body_ambiguous=True)
        self.assertIsNone(Guidance().update(value)['direction_hint'])

    def test_body_comparison_uses_mapped_camera_regions(self):
        value=frame();value['guidance_analysis'].update(direction_frame='BODY',
            native_body_levels=[3,2,1],native_body_ambiguous=False,
            native_region_mapping={'regions':[
                {'camera_region':'LEFT','body_direction':'RIGHT','ambiguous':False},
                {'camera_region':'CENTER','body_direction':'CENTER','ambiguous':False},
                {'camera_region':'RIGHT','body_direction':'LEFT','ambiguous':False}]})
        result=Guidance().update(value)
        self.assertEqual(result['direction_hint'],'RIGHT')
        self.assertEqual(result['standing_clearances_m'],[.68,1.2,2.98])

    def test_walking_route_policy_does_not_gain_a_depth_only_movement_candidate(self):
        guide=Guidance();guide.set_mode('WALKING')
        result=guide.update(frame())
        self.assertIsNone(result['candidate']);self.assertFalse(result['can_continue'])
        self.assertEqual(result['traffic_decision'],'STOP')


if __name__=='__main__':unittest.main()
