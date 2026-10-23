import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from body_calibration import BodyCalibration, map_native_regions, to_body_axes
from guidance import SceneAnalysis, corridor_candidates
from geometry import canonical_hash


def camera():
    value = {'width': 640, 'height': 480, 'fx': 400., 'fy': 400., 'cx': 320., 'cy': 240.,
             'distortion_model': 'NONE', 'distortion': [0.] * 8, 'serial': 'SYNTHETIC'}
    value['sha256'] = canonical_hash(value)
    return value


class BodyCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.camera = camera()
        self.calibration = BodyCalibration(lambda: self.now)
        self.info = {
            'state': 'STREAMING', 'frame_age_ms': 30, 'session_id': 'session',
            'geometry': {
                'state': 'READY', 'camera': self.camera,
                'ground_state': 'REFERENCE_MATCHED_CANDIDATE',
                'reference': {'sha256': 'reference-hash', 'height_m': .8,
                              'pitch_down_deg': 0, 'scope': 'STATIONARY_BENCH_ONLY'},
            },
        }

    def payload(self, **changes):
        value = {'measured': True, 'stationary': True,
                 'camera_forward_m': .1, 'camera_right_m': .05, 'yaw_deg': 10,
                 'required_passage_width_m': .7}
        value.update(changes)
        return value

    def test_requires_fresh_confirmed_ground_and_explicit_measurements(self):
        stale = dict(self.info, frame_age_ms=201)
        with self.assertRaises(ValueError):
            self.calibration.set(self.payload(), stale)
        unconfirmed = dict(self.info)
        unconfirmed['geometry'] = dict(self.info['geometry'], ground_state='PLANE_UNCONFIRMED')
        with self.assertRaises(ValueError):
            self.calibration.set(self.payload(), unconfirmed)
        with self.assertRaises(ValueError):
            self.calibration.set(self.payload(measured=False), self.info)
        for key, value in [('camera_forward_m', True), ('camera_right_m', math.nan),
                           ('yaw_deg', 60), ('yaw_deg', '10'),
                           ('required_passage_width_m', .2), ('required_passage_width_m', True)]:
            with self.assertRaises(ValueError):
                self.calibration.set(self.payload(**{key: value}), self.info)

    def test_measurement_is_session_and_camera_bound_and_expires(self):
        result = self.calibration.set(self.payload(), self.info)
        self.assertEqual(result['state'], 'SET')
        self.assertIsNotNone(self.calibration.read('session', self.camera['sha256']))
        self.assertIsNone(self.calibration.read('other', self.camera['sha256']))
        self.calibration.set(self.payload(), self.info)
        self.assertIsNone(self.calibration.read('session', 'changed'))
        self.calibration.set(self.payload(), self.info)
        self.now = result['expires_in_s'] + 11
        self.assertIsNone(self.calibration.read('session', self.camera['sha256']))

    def test_yaw_rotates_camera_forward_into_body_right(self):
        result = self.calibration.set(self.payload(camera_forward_m=0, camera_right_m=0), self.info)
        self.assertEqual(result['state'], 'SET')
        active = self.calibration.read('session', self.camera['sha256'])
        points = np.array([[0., 1., 1.]])  # one metre forward on the floor (camera Y points down)
        x, z, height = to_body_axes(points, {'normal': [0, -1, 0], 'offset_m': 1}, active)
        self.assertAlmostEqual(math.degrees(math.atan2(x[0], z[0])), 10, places=5)
        self.assertAlmostEqual(height[0], 0, places=6)

    def test_camera_offset_moves_body_origin_as_measured(self):
        result = self.calibration.set(self.payload(yaw_deg=0), self.info)
        self.assertEqual(result['state'], 'SET')
        active = self.calibration.read('session', self.camera['sha256'])
        x, z, _ = to_body_axes(np.array([[0., 1., 1.]]),
                               {'normal': [0, -1, 0], 'offset_m': 1}, active)
        self.assertAlmostEqual(x[0], .05)
        self.assertAlmostEqual(z[0], 1.1)

    def test_clear_and_missing_ground_are_explicit(self):
        self.calibration.set(self.payload(), self.info)
        self.assertEqual(self.calibration.set({'clear': True}, self.info)['state'], 'UNSET')
        self.assertIsNone(self.calibration.read('session', self.camera['sha256']))
        with self.assertRaises(ValueError):
            to_body_axes(np.zeros((1, 3)), None, self.payload())

    def test_transformed_point_cloud_scans_from_body_origin(self):
        self.calibration.set(self.payload(yaw_deg=0), self.info)
        active = self.calibration.read('session', self.camera['sha256'])
        x, z = np.meshgrid(np.arange(-1, 1.01, .04), np.arange(.6, 2.21, .04))
        floor = np.column_stack([x.ravel(), np.ones(x.size), z.ravel()])
        obstacle = np.array([[0., .5, 1.0], [.04, .5, 1.0]])
        body_points = np.vstack([floor, obstacle])
        camera_points = body_points.copy()
        camera_points[:, 0] -= .05
        camera_points[:, 2] -= .1
        plane = {'normal': [0, -1, 0], 'offset_m': 1}
        axes = to_body_axes(camera_points, plane, active)
        result = corridor_candidates(camera_points, np.ones(len(camera_points), bool),
                                     plane, True, axes)
        self.assertEqual(result[1]['direction'], 'CENTER')
        self.assertEqual(result[1]['state'], 'BLOCKED_NEAR')

    def test_scene_analysis_uses_body_frame_only_with_confirmed_ground(self):
        self.calibration.set(self.payload(), self.info)
        active = self.calibration.read('session', self.camera['sha256'])
        model = dict(self.camera, width=2, height=2, cx=.5, cy=.5, fx=1., fy=1.)
        pixels = np.array([[0, 0], [1, 0]], np.int32)
        rays = np.array([[0., .5, 1.], [0., .5, 1.]], np.float32)
        raw = np.full((2, 2), 1000, np.uint16)
        geometry = {'ground_state': 'REFERENCE_MATCHED_CANDIDATE',
                    'plane': {'normal': [0, -1, 0], 'offset_m': 1}}
        analysis = SceneAnalysis()
        analysis.motion.update = lambda *args: {'state': 'CAMERA_STILL'}
        with patch('guidance.make_rays', return_value=(pixels, rays)):
            result = analysis.evaluate(np.zeros((2, 2, 3), np.uint8), raw, 1., model,
                                       geometry, 1., 'session', active)
        self.assertEqual(result['direction_frame'], 'BODY')
        self.assertTrue(result['calibration_applied'])
        self.assertEqual(result['body_transform']['yaw_deg'], 10)
        self.assertEqual(result['required_passage_width_m'], .7)
        unconfirmed = dict(geometry, ground_state='PLANE_UNCONFIRMED')
        with patch('guidance.make_rays', return_value=(pixels, rays)), \
                patch.object(analysis.motion, 'update', return_value={'state': 'CAMERA_STILL'}):
            result = analysis.evaluate(np.zeros((2, 2, 3), np.uint8), raw, 1., model,
                                       unconfirmed, 2., 'session', active)
        self.assertEqual(result['direction_frame'], 'CAMERA')
        self.assertFalse(result['calibration_applied'])

    def test_native_depth_regions_map_to_body_directions(self):
        self.calibration.set(self.payload(yaw_deg=20, camera_forward_m=0, camera_right_m=0), self.info)
        active = self.calibration.read('session', self.camera['sha256'])
        native = [{'level': 1, 'distance_p10_m': 1.0},
                  {'level': 3, 'distance_p10_m': .8},
                  {'level': 1, 'distance_p10_m': 1.2}]
        mapped = map_native_regions(self.camera, active, native)
        self.assertEqual(mapped['directions'], ['LEFT', 'RIGHT', 'RIGHT'])
        self.assertEqual(mapped['body_levels'], [1, 0, 3])
        self.assertFalse(mapped['ambiguous'])

    def test_body_mount_ground_tracking_survives_camera_motion(self):
        self.calibration.set(self.payload(), self.info)
        active = self.calibration.read('session', self.camera['sha256'])
        self.assertAlmostEqual(active['height_m'], .8)
        model = dict(self.camera, width=2, height=2, cx=.5, cy=.5, fx=1., fy=1.)
        pixels = np.array([[0, 0], [1, 0]], np.int32)
        rays = np.array([[0., .5, 1.], [0., .5, 1.]], np.float32)
        raw = np.full((2, 2), 1000, np.uint16)
        geometry = {'ground_state': 'REFERENCE_MATCHED_CANDIDATE',
                    'plane': {'normal': [0, -1, 0], 'offset_m': 1}}
        analysis = SceneAnalysis()
        with patch('guidance.make_rays', return_value=(pixels, rays)), \
                patch.object(analysis.motion, 'update', return_value={'state': 'CAMERA_MOVING'}):
            result = analysis.evaluate(np.zeros((2, 2, 3), np.uint8), raw, 1., model,
                                       geometry, 3., 'session', active)
            without = SceneAnalysis()
            without.motion.update = lambda *args: {'state': 'CAMERA_MOVING'}
            rejected = without.evaluate(np.zeros((2, 2, 3), np.uint8), raw, 1., model,
                                        geometry, 3., 'session', None)
        self.assertEqual(result['direction_frame'], 'BODY')
        self.assertFalse(result['motion_invalidated'])
        self.assertEqual(result['motion_policy'], 'BODY_MOUNT_GROUND_TRACKING')
        self.assertTrue(rejected['motion_invalidated'])


if __name__ == '__main__':
    unittest.main()
