"""IMU-only gait state with hysteresis. It never rotates or localizes the user."""
import math
import json
import statistics
import time
from collections import deque
from pathlib import Path

import numpy as np

WINDOW_S=2.4
MIN_WALK_S=1.8
WALK_HOLD_S=.6
STILL_HOLD_S=1.5
LOST_WALK_S=1.0
DEFAULT_PROFILE={
    'still_std_max':.08,'still_gyro_max':.12,'walk_std_min':.10,
    'cadence_min_hz':.8,'cadence_max_hz':3.0,'interval_cv_max':.35,'walk_gyro_max':5.0}


def load_profile(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema')!='lumina-gait-profile-1' or not isinstance(data.get('thresholds'),dict):
        raise ValueError('Invalid gait profile')
    profile=dict(DEFAULT_PROFILE)
    for key in profile:
        value=data['thresholds'].get(key)
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Invalid '+key)
        profile[key]=float(value)
    if not 0<profile['still_std_max']<profile['walk_std_min']<1:
        raise ValueError('Invalid gait std thresholds')
    if not 0<profile['cadence_min_hz']<profile['cadence_max_hz']<=5:
        raise ValueError('Invalid cadence range')
    return profile


def _periodicity(samples,profile=None):
    profile=profile or DEFAULT_PROFILE
    if len(samples)<40:return None
    t=np.array([x[0] for x in samples]);mag=np.array([x[1] for x in samples])
    duration=t[-1]-t[0]
    if duration<MIN_WALK_S:return None
    uniform=np.arange(t[0],t[-1],.02)
    if len(uniform)<20:return None
    signal=np.interp(uniform,t,mag-float(np.mean(mag)))
    crossings=[]
    for index in range(1,len(signal)):
        if signal[index-1]<=0<signal[index]:crossings.append(uniform[index])
    intervals=np.diff(crossings)
    if len(intervals)<3:return None
    frequency=1/float(np.median(intervals))
    cv=float(np.std(intervals)/max(1e-6,np.mean(intervals)))
    return {'cadence_hz':round(frequency,2),'interval_cv':round(cv,2),
            'periodic':bool(profile['cadence_min_hz']<=frequency<=profile['cadence_max_hz']
                and cv<=profile['interval_cv_max'])}


class GaitDetector:
    def __init__(self,clock=time.monotonic,profile=None,profile_source='DEFAULT',profile_path=None):
        self.clock=clock;self.samples=deque();self.last_accel_ts=None
        self.yaw_samples=deque(maxlen=12)
        self.state='UNKNOWN';self.walk_since=None;self.still_since=None;self.lost_since=None
        self.metrics={};self.last_imu=None
        self.profile=dict(DEFAULT_PROFILE);self.profile.update(profile or {})
        self.profile_source=profile_source
        self.profile_path=Path(profile_path) if profile_path else None
        self.profile_mtime=None;self.profile_checked=0.

    def update(self,imu):
        self._maybe_reload()
        if not isinstance(imu,dict) or not imu.get('accel_valid'):
            self._reset_samples();return self.read()
        ts=imu.get('accel_ts_us')
        if not isinstance(ts,int) or ts==self.last_accel_ts:return self.read()
        self.last_accel_ts=ts;self.last_imu=imu
        a=imu.get('accel') or {};g=imu.get('gyro') or {}
        try:
            ax,ay,az=float(a['x']),float(a['y']),float(a['z'])
        except (KeyError,TypeError,ValueError):
            return self.read()
        if not all(math.isfinite(v) for v in (ax,ay,az)):return self.read()
        t=ts/1e6;magnitude=math.sqrt(ax*ax+ay*ay+az*az)
        gyro_rms=(math.sqrt(sum(float(g.get(k,0))**2 for k in ('x','y','z')))
            if imu.get('gyro_valid') else 0.)
        yaw_rate=0.
        if imu.get('gyro_valid') and magnitude>1e-6:
            try:
                gx,gy,gz=(float(g.get(k,0)) for k in ('x','y','z'))
                yaw_rate=math.degrees((gx*ax+gy*ay+gz*az)/magnitude)
                self.yaw_samples.append(yaw_rate)
            except (TypeError,ValueError):pass
        else:self.yaw_samples.clear()
        self.samples.append((t,magnitude,gyro_rms))
        while self.samples and t-self.samples[0][0]>WINDOW_S:self.samples.popleft()
        now=self.clock();span=t-self.samples[0][0] if self.samples else 0.
        values=[v[1] for v in self.samples];gyros=[v[2] for v in self.samples]
        std=statistics.pstdev(values) if len(values)>2 else None
        gyro_mean=statistics.mean(gyros) if gyros else 0.
        period=_periodicity(self.samples,self.profile)
        walking=bool(span>=MIN_WALK_S and std is not None
            and self.profile['walk_std_min']<=std<=2.5
            and period and period['periodic'] and gyro_mean<=self.profile['walk_gyro_max'])
        still=bool(span>=STILL_HOLD_S and std is not None and std<self.profile['still_std_max']
            and gyro_mean<self.profile['still_gyro_max'])
        if walking:
            self.walk_since=self.walk_since or now;self.lost_since=None
        else:
            self.walk_since=None
            if self.state=='WALKING':self.lost_since=self.lost_since or now
        if still:
            self.still_since=self.still_since or now
        else:self.still_since=None
        if walking and now-self.walk_since>=WALK_HOLD_S:
            self.state='WALKING'
        elif still and now-self.still_since>=STILL_HOLD_S:
            self.state='STANDING'
        elif self.state=='WALKING' and self.lost_since and now-self.lost_since>=LOST_WALK_S:
            self.state='UNKNOWN'
        self.metrics={'accel_mag_mean':round(statistics.mean(values),3) if values else None,
            'accel_mag_std':round(std,3) if std is not None else None,
            'gyro_mean_rad_s':round(gyro_mean,3),
            'cadence_hz':period['cadence_hz'] if period and period['periodic'] else None,
            'interval_cv':period['interval_cv'] if period and period['periodic'] else None,
            'periodic':bool(period and period['periodic']),
            'window_s':round(span,2),
            'turn_rate_dps':round(statistics.median(self.yaw_samples),1) if self.yaw_samples else None}
        return self.read()

    def _reset_samples(self):
        self.samples.clear();self.yaw_samples.clear();self.last_accel_ts=None
        self.state='UNKNOWN';self.walk_since=None;self.still_since=None;self.lost_since=None
        self.metrics={};self.last_imu=None

    def _maybe_reload(self):
        if self.profile_path is None:return
        now=self.clock()
        if now-self.profile_checked<1.:return
        self.profile_checked=now
        try:
            mtime=self.profile_path.stat().st_mtime_ns
        except FileNotFoundError:
            if self.profile_mtime is None and self.profile_source=='DEFAULT':return
            self.profile=dict(DEFAULT_PROFILE);self.profile_source='DEFAULT'
            self.profile_mtime=None;self._reset_samples();return
        except OSError:
            return
        if mtime==self.profile_mtime:return
        try:
            profile=load_profile(self.profile_path)
        except (OSError,ValueError):
            self.profile=dict(DEFAULT_PROFILE);self.profile_source='DEFAULT_INVALID_PROFILE'
            self.profile_mtime=mtime
            self._reset_samples()
            return
        self.profile=profile;self.profile_source='CALIBRATED';self.profile_mtime=mtime
        self._reset_samples()

    def read(self):
        imu=self.last_imu or {}
        return {'state':self.state,'source':'CAMERA_IMU_NOT_LOCALIZATION',
            'confidence':('HIGH' if self.state in ('WALKING','STANDING') else 'UNKNOWN'),
            'metrics':dict(self.metrics),'age_ms':imu.get('age_ms'),
            'profile_source':self.profile_source,'thresholds':dict(self.profile),
            'accel_valid':bool(imu.get('accel_valid')),'gyro_valid':bool(imu.get('gyro_valid')),
            'accel':imu.get('accel'),'gyro':imu.get('gyro')}


def profile_from_samples(still_samples,walk_samples):
    """Build conservative thresholds from explicit standing/walking captures."""
    if len(still_samples)<100 or len(walk_samples)<200:raise ValueError('Not enough IMU samples')
    still_mag=[x[1] for x in still_samples];walk_mag=[x[1] for x in walk_samples]
    still_gyro=[x[2] for x in still_samples];walk_gyro=[x[2] for x in walk_samples]
    still_std=statistics.pstdev(still_mag);walk_std=statistics.pstdev(walk_mag)
    still_gyro_mean=statistics.mean(still_gyro);period=_periodicity(walk_samples)
    if not period or not period['periodic']:raise ValueError('Walking cadence is not periodic')
    if walk_std<=max(.12,still_std*2):raise ValueError('Walking signal is not distinct from standing')
    thresholds={
        'still_std_max':round(min(.15,max(.06,still_std*3.5)),3),
        'still_gyro_max':round(min(.30,max(.08,still_gyro_mean*4)),3),
        'walk_std_min':round(min(.40,max(.10,still_std*5,walk_std*.45)),3),
        'cadence_min_hz':round(max(.6,period['cadence_hz']*.8),2),
        'cadence_max_hz':round(min(3.5,period['cadence_hz']*1.2),2),
        'interval_cv_max':round(max(.30,min(.45,period['interval_cv']*1.4)),2),
        'walk_gyro_max':round(max(1.0,statistics.mean(walk_gyro)*4),2)}
    return {'schema':'lumina-gait-profile-1','thresholds':thresholds,
        'metrics':{'still_accel_std':round(still_std,3),'still_gyro_mean':round(still_gyro_mean,3),
            'walk_accel_std':round(walk_std,3),'walk_cadence_hz':period['cadence_hz'],
            'walk_interval_cv':period['interval_cv'],'still_samples':len(still_samples),
            'walk_samples':len(walk_samples)}}
