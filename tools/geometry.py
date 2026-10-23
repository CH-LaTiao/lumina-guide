"""Bounded camera-space geometry diagnostics; never authorizes movement/output."""
import ctypes
import hashlib
import json
import math
import threading
import time

import cv2
import numpy as np

ALGORITHM = 'geometry-diagnostic-1'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def camera_model(profile, serial):
    intr = profile.get_intrinsic()
    dist = profile.get_distortion()
    model = {'serial': serial, 'width': int(intr.width), 'height': int(intr.height),
             **{k: float(getattr(intr, k)) for k in ('fx', 'fy', 'cx', 'cy')},
             'distortion_model': str(dist.model).split('.')[-1],
             'distortion': [float(getattr(dist, k)) for k in ('k1', 'k2', 'p1', 'p2', 'k3', 'k4', 'k5', 'k6')],
             'source': 'SDK aligned depth stream profile', 'coordinate': 'camera_optical'}
    model['sha256'] = canonical_hash(model)
    return model


def make_rays(model, stride=8):
    w, h = model['width'], model['height']
    values = [model[k] for k in ('fx', 'fy', 'cx', 'cy')] + model['distortion']
    if not (0 < w <= 1920 and 0 < h <= 1080 and all(math.isfinite(v) for v in values)
            and model['fx'] > 0 and model['fy'] > 0 and 0 <= model['cx'] < w and 0 <= model['cy'] < h):
        raise ValueError('Invalid camera intrinsics')
    if model['distortion_model'] not in ('NONE', 'BROWN_CONRADY', 'BROWN_CONRADY_K6'):
        raise ValueError('Unsupported SDK distortion model')
    yy, xx = np.mgrid[stride//2:h:stride, stride//2:w:stride]
    pixels = np.stack((xx, yy), -1).reshape(-1, 2).astype(np.float32)
    k = np.array([[model['fx'], 0, model['cx']], [0, model['fy'], model['cy']], [0, 0, 1]], np.float64)
    coefficients = np.zeros(8) if model['distortion_model'] == 'NONE' else np.array(model['distortion'])
    if model['distortion_model'] == 'BROWN_CONRADY':
        coefficients = coefficients[:5]
    xy = cv2.undistortPoints(pixels[:, None, :], k, coefficients).reshape(-1, 2)
    rays = np.column_stack((xy, np.ones(len(xy)))).astype(np.float32)
    if not np.isfinite(rays).all():
        raise ValueError('Invalid deprojection rays')
    return pixels.astype(np.int32), rays


class BenchReference:
    """Explicit measurements bound to current camera session, RAM only, 120s lease."""
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
        if (info.get('state') != 'STREAMING' or info.get('frame_age_ms', 9999) > 200
                or not geometry.get('camera') or geometry.get('state') != 'READY'):
            raise ValueError('Fresh geometry frame required')
        if payload.get('measured') is not True or payload.get('stationary') is not True:
            raise ValueError('Measured height and stationary level mounting must be confirmed')
        height, pitch = payload.get('height_m'), payload.get('pitch_down_deg')
        if (type(height) not in (int, float) or type(pitch) not in (int, float)
                or not math.isfinite(height) or not math.isfinite(pitch)
                or not .2 <= height <= 2.5 or not -20 <= pitch <= 60):
            raise ValueError('Height must be 0.2..2.5m and pitch -20..60deg')
        angle = math.radians(pitch)
        value = {'height_m': height, 'pitch_down_deg': pitch,
                 'up_camera': [0, -math.cos(angle), -math.sin(angle)],
                 'session_id': info['session_id'], 'camera_sha256': geometry['camera']['sha256'],
                 'created': self.clock(), 'expires': self.clock()+120, 'scope': 'STATIONARY_BENCH_ONLY'}
        value['sha256'] = canonical_hash(value)
        with self.lock:
            self.value = value
        return {'state': 'SET', 'expires_in_s': 120, 'sha256': value['sha256']}

    def read(self, session, camera_hash):
        with self.lock:
            value = self.value
            if not value or self.clock() >= value['expires']:
                self.value = None
                return None
            if value['session_id'] != session or value['camera_sha256'] != camera_hash:
                self.value = None
                return None
            return dict(value)


def fit_plane(points, up, height=None):
    """Deterministic RANSAC on valid lower-image points; reject walls/poor support."""
    if len(points) < 80:
        return None
    # Bound fitting work even when a future stream has a larger sampling grid.
    points = points[::max(1, math.ceil(len(points)/1800))]
    up = np.asarray(up, np.float64)
    max_angle = 15 if height is not None else 45
    threshold = math.cos(math.radians(max_angle))
    rng = np.random.default_rng(20261018)
    best = None
    for _ in range(50):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b-a, c-a)
        length = np.linalg.norm(n)
        if length < 1e-6:
            continue
        n = n/length
        if np.dot(n, up) < 0:
            n = -n
        d = -float(np.dot(n, a))
        if np.dot(n, up) < threshold or not .15 <= d <= 2.7:
            continue
        if height is not None and abs(d-height) > .12:
            continue
        residual = np.abs(points[:, 0]*n[0]+points[:, 1]*n[1]+points[:, 2]*n[2]+d)
        mask = residual <= .03
        count = int(mask.sum())
        if best is None or count > best[0]:
            best = (count, mask)
    if best is None or best[0]/len(points) < .7:
        return None
    inliers = points[best[1]]
    center = inliers.mean(axis=0)
    _, singular, vt = np.linalg.svd(inliers-center, full_matrices=False)
    if singular[1]/math.sqrt(len(inliers)) < .08:
        return None  # A line or tiny patch does not support a floor plane.
    n = vt[-1]
    if np.dot(n, up) < 0:
        n = -n
    d = -float(np.dot(n, center))
    residual = np.abs(points[:, 0]*n[0]+points[:, 1]*n[1]+points[:, 2]*n[2]+d)
    mask = residual <= .03
    if (mask.mean() < .7 or np.dot(n, up) < threshold or not .15 <= d <= 2.7
            or (height is not None and abs(d-height) > .12)):
        return None
    return {'normal': n.tolist(), 'offset_m': d, 'inlier_ratio': float(mask.mean()),
            'residual_p90_m': float(np.percentile(residual[mask], 90)), 'fit_points': len(points)}


class Geometry:
    def __init__(self, model, native, reference, body_calibration=None):
        self.model, self.reference = model, reference
        self.body_calibration = body_calibration
        self.pixels, self.rays = make_rays(model)
        self.previous = None
        self.stable = 0
        self.last_id = None
        self.session = None
        self.last_index = -1
        self.last_ref = None
        self.fn = native.lib.lumina_geometry_clusters
        self.fn.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_uint8),
                           ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.fn.restype = ctypes.c_int

    def evaluate(self, raw, scale_mm, age_ms, frame_id, session, ground_line_y=None):
        started = time.monotonic()
        result = {'state': 'READY', 'algorithm': ALGORITHM, 'camera': self.model,
                  'frame_id': frame_id, 'diagnostic_only': True, 'movement_allowed': False,
                  'ground_state': 'UNKNOWN', 'body_calibration': 'UNSET', 'objects': [],
                  'plane': None, 'reference': None, 'stable_frames': 0}
        pixels = self.pixels
        if session != self.session:
            self.session, self.last_index = session, -1
            self.previous, self.stable = None, 0
        try:
            prefix, index_text = frame_id.rsplit(':', 1)
            index = int(index_text)
            if prefix != session or index < 0:
                raise ValueError('Invalid frame identity')
        except (ValueError,AttributeError):
            result['state'] = 'INVALID_FRAME_ID'
            return result, np.zeros(len(pixels), bool)
        if index <= self.last_index:
            result.update(state='DUPLICATE_OR_REORDERED',stable_frames=self.stable)
            return result, np.zeros(len(pixels), bool)
        if (raw.shape != (self.model['height'], self.model['width']) or not math.isfinite(scale_mm)
                or scale_mm <= 0 or not math.isfinite(age_ms) or not 0 <= age_ms <= 200):
            self.previous = None
            self.stable = 0
            result['state'] = 'INVALID_OR_STALE'
            return result, np.zeros(len(pixels), bool)
        self.last_index = index
        z = raw[pixels[:, 1], pixels[:, 0]].astype(np.float32)*scale_mm/1000
        valid = np.isfinite(z) & (z >= .3) & (z <= 4)
        points = np.ascontiguousarray(self.rays*z[:, None], dtype=np.float32)
        line_y=(float(ground_line_y) if type(ground_line_y) in (int,float) and math.isfinite(ground_line_y)
            else self.model['height']*.55)
        ground_region=pixels[:,1]>=line_y
        ground_samples=int(ground_region.sum())
        lower_coverage=float(valid[ground_region].mean()) if ground_samples else 0.
        reference = (self.body_calibration.read(session, self.model['sha256'])
                     if self.body_calibration else None)
        reference_source = 'BODY_MOUNT' if reference else 'BENCH'
        if reference is None:
            reference = self.reference.read(session, self.model['sha256'])
        reference_hash = reference['sha256'] if reference else None
        if reference_hash != self.last_ref:
            self.previous = None
            self.stable = 0
            self.last_ref = reference_hash
        plane = fit_plane(points[valid & ground_region], reference['up_camera'] if reference else [0, -1, 0],
                          reference['height_m'] if reference else None) if lower_coverage >= .6 and ground_samples>=80 else None
        plane_done = time.monotonic()
        same = bool(plane and self.previous and abs(plane['offset_m']-self.previous['offset_m']) <= .05
                    and np.dot(plane['normal'], self.previous['normal']) >= math.cos(math.radians(5)))
        if frame_id != self.last_id:
            self.stable = self.stable+1 if same else (1 if plane else 0)
        self.last_id, self.previous = frame_id, plane
        ground = bool(reference and plane and self.stable >= 5)
        result.update(plane=plane, stable_frames=self.stable,
                      reference={'sha256': reference_hash, 'scope': reference['scope'],
                                 'source': reference_source,
                                 'height_m': reference['height_m'],
                                 'pitch_down_deg': reference['pitch_down_deg'],
                                 'expires_in_s': reference.get('expires_in_s',
                                     max(0,reference['expires']-self.reference.clock()))} if reference else None,
                      ground_state='REFERENCE_MATCHED_CANDIDATE' if ground else ('PLANE_UNCONFIRMED' if plane else 'UNKNOWN'),
                      counts={'sampled': len(z), 'valid': int(valid.sum()), 'invalid': int((z == 0).sum()),
                              'unknown_near': int(((z > 0) & (z < .3)).sum()), 'out_of_range': int((z > 4).sum())},
                      lower_valid_ratio=lower_coverage,
                      ground_line={'line_y':round(line_y,1),'ground_samples':ground_samples,
                          'ground_valid_ratio':round(lower_coverage,4)})
        signed = np.zeros(len(points), np.float32)
        if plane:
            n = plane['normal']
            signed = points[:, 0]*n[0]+points[:, 1]*n[1]+points[:, 2]*n[2]+plane['offset_m']
        plane_mask = valid & (np.abs(signed) <= .03) if plane else np.zeros(len(z), bool)
        # An unconfirmed table/plane must never remove raw obstacle evidence.
        eligible = np.ascontiguousarray(valid & (~plane_mask if ground else True), dtype=np.uint8)
        labels = np.full(len(z), -1, dtype=np.int32)
        count = self.fn(points.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                        eligible.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)), len(z),
                        labels.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
        if count < 0:
            raise ValueError('Native geometry clustering failed')
        cluster_done = time.monotonic()
        objects = []
        # Per-cluster numpy statistics cost ~0.7ms each; only the nearest few can
        # reach the returned top-12 list, so rank by min depth first (exact) and
        # compute full statistics only for those candidates.
        candidates = range(count)
        clustered = np.flatnonzero(labels >= 0)
        if clustered.size:
            order = np.argsort(labels[clustered], kind='stable')
            ordered = clustered[order]
            ordered_labels = labels[ordered]
            starts = np.flatnonzero(np.r_[True, ordered_labels[1:] != ordered_labels[:-1]])
            nearest = np.minimum.reduceat(points[ordered, 2], starts)
            present = ordered_labels[starts]
            candidates = [int(v) for v in present[np.argsort(nearest)[:18]]]
        for label in candidates:
            mask = labels == label
            p, uv = points[mask], pixels[mask]
            distance = float(np.percentile(p[:, 2], 20))
            x = float(np.median(p[:, 0]))
            objects.append({'cluster_id': label, 'sample_points': int(mask.sum()),
                            'bbox': [int(uv[:, 0].min()), int(uv[:, 1].min()), int(uv[:, 0].max()), int(uv[:, 1].max())],
                            'depth_z_p20_m': distance,
                            'camera_side': 'LEFT' if x < -.15 else ('RIGHT' if x > .15 else 'CENTER'),
                            'camera_up_range_m': [float(np.percentile(-p[:, 1], 10)), float(np.percentile(-p[:, 1], 90))],
                            'height_over_plane_m': [float(np.percentile(signed[mask], 10)), float(np.percentile(signed[mask], 90))] if ground else None,
                            'level': 'URGENT_CANDIDATE' if distance <= .8 else ('NEAR_CANDIDATE' if distance <= 1.5 else 'OBSERVE')})
        result['cluster_count'] = count
        result['objects'] = sorted(objects, key=lambda o: (o['depth_z_p20_m'], o['cluster_id']))[:12]
        result['removed_plane_samples'] = int(plane_mask.sum()) if ground else 0
        result['timings_ms'] = {'plane': round((plane_done-started)*1000, 1),
            'cluster': round((cluster_done-plane_done)*1000, 1),
            'objects': round((time.monotonic()-cluster_done)*1000, 1)}
        result['compute_ms'] = (time.monotonic()-started)*1000
        return result, plane_mask

    def overlay(self, rgb, result, plane_mask):
        image = rgb.copy()
        for u, v in self.pixels[plane_mask]:
            cv2.circle(image, (int(u), int(v)), 2, (180, 180, 40), -1)
        for obj in result['objects']:
            x0, y0, x1, y1 = obj['bbox']
            color = (50, 130, 255) if obj['depth_z_p20_m'] <= 1.5 else (230, 190, 80)
            cv2.rectangle(image, (x0, y0), (x1, y1), color, 1)
            cv2.putText(image, f"G{obj['cluster_id']} z {obj['depth_z_p20_m']:.2f}m", (x0, max(72, y0-4)),
                        cv2.FONT_HERSHEY_SIMPLEX, .42, color, 1, cv2.LINE_AA)
        return image
