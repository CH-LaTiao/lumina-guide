"""Explicit camera-to-body calibration and horizontal point transformation.

Calibration is a measured mount model, not a safety or movement authorization.
It is RAM-only, bound to one camera session/intrinsics hash, and never inferred
from camera motion.
"""
import math
import threading
import time

import numpy as np

from geometry import canonical_hash

LEASE_S = 30 * 60
YAW_UNCERTAINTY_DEG = 5.0
POSITION_UNCERTAINTY_M = 0.05


def _finite_number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be {low}..{high}')
    return float(value)


def camera_ground_axes(points, plane):
    """Project points to right/forward/up axes derived from the fitted floor."""
    if not plane:
        raise ValueError('Ground plane required for body coordinates')
    up = np.asarray(plane['normal'], float)
    forward = np.array([0., 0., 1.])
    forward -= np.dot(forward, up) * up
    length = float(np.linalg.norm(forward))
    if length < 1e-6:
        raise ValueError('Ground normal is parallel to camera forward')
    forward /= length
    right = np.cross(forward, up)
    return points @ right, points @ forward, points @ up + plane['offset_m']


def to_body_axes(points, plane, calibration):
    """Transform camera points to body-centred ground coordinates.

    Positive yaw means the camera optical forward points right of body forward.
    Positive forward/right offsets mean the lens is ahead/right of body centre.
    """
    x, z, height = camera_ground_axes(points, plane)
    yaw = math.radians(calibration['yaw_deg'])
    cosine, sine = math.cos(yaw), math.sin(yaw)
    body_x = x * cosine + z * sine + calibration['camera_right_m']
    body_z = -x * sine + z * cosine + calibration['camera_forward_m']
    return body_x, body_z, height


def map_native_regions(model, calibration, native):
    """Approximate image-third depth regions in body directions using region P10."""
    yaw = math.radians(calibration['yaw_deg'])
    cosine, sine = math.cos(yaw), math.sin(yaw)
    result = []
    directions = []
    for index in range(3):
        u = model['width'] * (index + .5) / 3
        distance = (native[index].get('distance_p10_m') if index < len(native) else None)
        z = float(distance) if isinstance(distance, (int, float)) and distance > 0 else 1.
        x = (u - model['cx']) / model['fx'] * z
        body_x = x * cosine + z * sine + calibration['camera_right_m']
        body_z = -x * sine + z * cosine + calibration['camera_forward_m']
        angle = math.degrees(math.atan2(body_x, body_z)) if body_z > .05 else None
        if angle is None:
            direction = 'UNKNOWN'
        elif angle < -8:
            direction = 'LEFT'
        elif angle > 8:
            direction = 'RIGHT'
        else:
            direction = 'CENTER'
        ambiguous = angle is None or min(abs(angle - 8), abs(angle + 8)) <= YAW_UNCERTAINTY_DEG
        level = native[index].get('level', 0) if index < len(native) else 0
        result.append({'camera_region': ('LEFT', 'CENTER', 'RIGHT')[index],
                       'body_angle_deg': round(angle, 1) if angle is not None else None,
                       'body_direction': direction, 'ambiguous': ambiguous, 'level': level,
                       'distance_p10_m': distance})
        directions.append(direction)
    levels = {'LEFT': 0, 'CENTER': 0, 'RIGHT': 0}
    for row in result:
        direction = row['body_direction'] if row['body_direction'] in levels else 'CENTER'
        levels[direction] = max(levels[direction], row['level'])
    return {'regions': result, 'directions': directions,
            'body_levels': [levels['LEFT'], levels['CENTER'], levels['RIGHT']],
            'ambiguous': any(row['ambiguous'] and row['level'] == 3 for row in result)}


class BodyCalibration:
    """Measured mount transform bound to the current camera identity."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.value = None

    def set(self, payload, info):
        if payload.get('clear') is True:
            with self.lock:
                self.value = None
            return {'state': 'UNSET'}
        geometry = info.get('geometry') or {}
        reference = geometry.get('reference') or {}
        if (info.get('state') != 'STREAMING' or info.get('frame_age_ms', 9999) > 200
                or geometry.get('state') != 'READY' or not geometry.get('camera')
                or geometry.get('ground_state') != 'REFERENCE_MATCHED_CANDIDATE'
                or not reference.get('sha256')):
            raise ValueError('Fresh confirmed ground reference required')
        if payload.get('measured') is not True or payload.get('stationary') is not True:
            raise ValueError('Measured mount geometry and fixed camera must be confirmed')
        forward = _finite_number(payload.get('camera_forward_m'), 'Camera forward offset', -.5, .5)
        right = _finite_number(payload.get('camera_right_m'), 'Camera right offset', -.5, .5)
        yaw = _finite_number(payload.get('yaw_deg'), 'Camera yaw', -45, 45)
        passage_width = _finite_number(payload.get('required_passage_width_m'),
                                       'Required passage width with cane', .35, 1.5)
        height = _finite_number(reference.get('height_m'), 'Reference height', .2, 2.5)
        pitch = _finite_number(reference.get('pitch_down_deg'), 'Reference pitch', -20, 60)
        pitch_radians = math.radians(pitch)
        value = {
            'camera_forward_m': forward,
            'camera_right_m': right,
            'yaw_deg': yaw,
            'height_m': height,
            'pitch_down_deg': pitch,
            'up_camera': [0, -math.cos(pitch_radians), -math.sin(pitch_radians)],
            'required_passage_width_m': passage_width,
            'yaw_uncertainty_deg': YAW_UNCERTAINTY_DEG,
            'position_uncertainty_m': POSITION_UNCERTAINTY_M,
            'session_id': info['session_id'],
            'camera_sha256': geometry['camera']['sha256'],
            'reference_sha256': reference['sha256'],
            'created': self.clock(),
            'expires': self.clock() + LEASE_S,
            'scope': 'MANUAL_MOUNT_TO_BODY_CANDIDATE',
            'movement_allowed': False,
        }
        value['sha256'] = canonical_hash(value)
        with self.lock:
            self.value = value
        return {'state': 'SET', 'expires_in_s': LEASE_S, 'sha256': value['sha256']}

    def read(self, session, camera_hash):
        with self.lock:
            value = self.value
            if not value or self.clock() >= value['expires']:
                self.value = None
                return None
            if value['session_id'] != session or value['camera_sha256'] != camera_hash:
                self.value = None
                return None
            result = dict(value)
            result['state'] = 'ACTIVE'
            result['expires_in_s'] = max(0., result['expires'] - self.clock())
            return result
