"""Explicit standing/walking IMU calibration. Does not drive or move hardware."""
import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from gait import profile_from_samples
from imu_shm import ImuShm
from runtime_paths import runtime_dir


def collect(seconds):
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


def main():
    parser=argparse.ArgumentParser(description='Calibrate standing/walking IMU thresholds on the local device.')
    parser.add_argument('--stand-seconds',type=float,default=5.)
    parser.add_argument('--walk-seconds',type=float,default=8.)
    parser.add_argument('--output',type=Path,default=runtime_dir()/'gait-calibration.json')
    parser.add_argument('--confirm',action='store_true')
    args=parser.parse_args()
    if not 3<=args.stand_seconds<=15 or not 5<=args.walk_seconds<=30:parser.error('invalid duration')
    if args.output.exists():parser.error('Gait calibration exists; choose a new output name')
    if not args.confirm:
        print(json.dumps({'ready':False,'instructions':[
            '在有陪护、无障碍、可随时停下的安全直道进行。',
            f'先保持站定{args.stand_seconds:g}秒，再按提示步行{args.walk_seconds:g}秒。',
            '助手不得跟随进入相机近场；设备安装必须保持不变。',
            '重新运行并增加--confirm才会采样。'],'output':str(args.output)},ensure_ascii=False,indent=2))
        return
    try:
        input('站定准备：确认周围安全后按 Enter 开始静止采样…')
        print(f'静止采样 {args.stand_seconds:g} 秒…',flush=True)
        still=collect(args.stand_seconds)
        input('步行准备：在安全直道上按 Enter 后开始自然步行…')
        print(f'步行采样 {args.walk_seconds:g} 秒…',flush=True)
        walk=collect(args.walk_seconds)
        profile=profile_from_samples(still,walk)
    except (OSError,ValueError,RuntimeError,EOFError) as exc:
        parser.error(str(exc))
    profile['created_utc']=datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix(args.output.suffix+'.tmp')
    temporary.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'state':'CALIBRATED','output':str(args.output),**profile},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
