"""Two-phase local gait calibration shared by the web UI and tests."""
import json
import math
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from gait import profile_from_samples
from imu_shm import ImuShm


def collect_imu(seconds):
    reader=ImuShm();samples=[];last_ts=None;deadline=time.monotonic()+seconds
    try:
        while time.monotonic()<deadline:
            value=reader.latest()
            if value and value.get('accel_valid') and value['accel_ts_us']!=last_ts:
                last_ts=value['accel_ts_us'];a=value['accel'];g=value['gyro']
                mag=math.sqrt(a['x']**2+a['y']**2+a['z']**2)
                gyro=math.sqrt(g['x']**2+g['y']**2+g['z']**2) if value.get('gyro_valid') else 0.
                samples.append((last_ts/1e6,mag,gyro))
            time.sleep(.01)
    finally:reader.close()
    if len(samples)<seconds*40:raise RuntimeError('IMU sample rate too low')
    return samples


class GaitCalibrationManager:
    def __init__(self,output,collector=collect_imu,notify=None,clock=time.monotonic):
        self.output=Path(output);self.collector=collector;self.notify=notify or (lambda text:None)
        self.clock=clock;self.lock=threading.Lock();self.state='IDLE'
        self.still=None;self.error=None;self.started=0.;self.duration=0.;self.profile=None

    def _notify(self,text):
        try:self.notify(text)
        except Exception:pass

    def start_still(self,consent,safe_environment,seconds=5.):
        if consent is not True or safe_environment is not True:
            raise ValueError('步态标定需要安全场地和助手确认')
        with self.lock:
            if self.state=='COLLECTING_STILL':raise ValueError('静止采样进行中')
            self.state='COLLECTING_STILL';self.error=None;self.still=None
            self.started=self.clock();self.duration=float(seconds)
        self._notify('请保持设备稳定并站定，开始静止采样。')
        threading.Thread(target=self._still_worker,args=(float(seconds),),daemon=True).start()
        return self.read()

    def _still_worker(self,seconds):
        try:
            samples=self.collector(seconds)
            with self.lock:self.still=samples;self.state='STILL_READY';self.duration=0.
            self._notify('静止采样完成。请在安全直道准备好后开始步行采样。')
        except Exception as exc:
            with self.lock:self.state='ERROR';self.duration=0.;self.error=type(exc).__name__+': '+str(exc)[:100]

    def start_walk(self,consent,safe_environment,seconds=8.):
        if consent is not True or safe_environment is not True:
            raise ValueError('步态标定需要安全场地和助手确认')
        with self.lock:
            if self.state!='STILL_READY' or not self.still:raise ValueError('请先完成静止采样')
            self.state='COLLECTING_WALK';self.started=self.clock();self.duration=float(seconds)
        self._notify('开始自然步行采样。')
        threading.Thread(target=self._walk_worker,args=(float(seconds),),daemon=True).start()
        return self.read()

    def _walk_worker(self,seconds):
        try:
            with self.lock:still=self.still
            walk=self.collector(seconds)
            profile=profile_from_samples(still,walk)
            profile['created_utc']=datetime.now(timezone.utc).isoformat()
            self.output.parent.mkdir(parents=True,exist_ok=True)
            if self.output.exists():shutil.copy2(self.output,self.output.with_suffix('.previous.json'))
            temporary=self.output.with_suffix(self.output.suffix+'.tmp')
            temporary.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.output)
            with self.lock:self.profile=profile;self.state='COMPLETE';self.duration=0.
            self._notify('步态标定完成，新的个人阈值已生效。')
        except Exception as exc:
            with self.lock:self.state='ERROR';self.duration=0.;self.error=type(exc).__name__+': '+str(exc)[:100]

    def reset(self):
        with self.lock:
            if self.state.startswith('COLLECTING'):raise ValueError('采样进行中')
            self.state='IDLE';self.still=None;self.profile=None;self.error=None;self.duration=0.
        return self.read()

    def read(self):
        with self.lock:
            remaining=max(0.,self.duration-(self.clock()-self.started)) if self.duration else 0.
            return {'state':self.state,'still_complete':bool(self.still),'remaining_s':round(remaining,1),
                'error':self.error,'output':str(self.output),
                'profile':self.profile,'profile_ready':self.output.is_file()}
