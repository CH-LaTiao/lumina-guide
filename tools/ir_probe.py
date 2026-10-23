"""Inspect Gemini 336L stream profiles and IR/depth coexistence.

Diagnostic only: it never feeds perception or output. Stop the preview first,
because the camera allows a single owner.
"""
import argparse
import time

import pyorbbecsdk as ob

SENSORS=(
    ('DEPTH',ob.OBSensorType.DEPTH_SENSOR),
    ('COLOR',ob.OBSensorType.COLOR_SENSOR),
    ('IR',ob.OBSensorType.IR_SENSOR),
    ('LEFT_IR',ob.OBSensorType.LEFT_IR_SENSOR),
    ('RIGHT_IR',ob.OBSensorType.RIGHT_IR_SENSOR),
    ('CONFIDENCE',ob.OBSensorType.CONFIDENCE_SENSOR),
)

def list_profiles(pipeline):
    for name,kind in SENSORS:
        try:profiles=pipeline.get_stream_profile_list(kind)
        except Exception as exc:
            print(name,'unavailable',type(exc).__name__);continue
        rows=[]
        for index in range(profiles.get_count()):
            try:video=profiles.get_stream_profile_by_index(index).as_video_stream_profile()
            except Exception:continue
            rows.append(f'{video.get_width()}x{video.get_height()}@{video.get_fps()}:{video.get_format()}')
        print(f'{name} ({len(rows)}):',', '.join(rows[:14]))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--start',action='store_true');parser.add_argument('--seconds',type=float,default=5)
    parser.add_argument('--with-color',action='store_true')
    args=parser.parse_args()
    ctx=ob.Context()
    devices=ctx.query_devices()
    print('devices',len(devices))
    if len(devices)==0:raise SystemExit('no device')
    device=devices.get_device_by_index(0);info=device.get_device_info()
    print('name',info.get_name(),'serial',info.get_serial_number())
    pipeline=ob.Pipeline(device);list_profiles(pipeline)
    if not args.start:return
    config=ob.Config()
    requested=[(ob.OBSensorType.DEPTH_SENSOR,ob.OBFormat.Y16),
                     (ob.OBSensorType.LEFT_IR_SENSOR,ob.OBFormat.Y8),
                     (ob.OBSensorType.RIGHT_IR_SENSOR,ob.OBFormat.Y8)]
    if args.with_color:requested.append((ob.OBSensorType.COLOR_SENSOR,ob.OBFormat.MJPG))
    for kind,fmt in requested:
        profiles=pipeline.get_stream_profile_list(kind);chosen=None
        for index in range(profiles.get_count()):
            video=profiles.get_stream_profile_by_index(index).as_video_stream_profile()
            if video.get_width()==640 and video.get_height()==480 and video.get_fps()==30 and video.get_format()==fmt:
                chosen=video;break
        if chosen is not None:config.enable_stream(chosen)
    started=time.monotonic();pipeline.start(config)
    try:
        seen={}
        while time.monotonic()-started<args.seconds:
            frames=pipeline.wait_for_frames(500)
            if frames is None:continue
            for kind,label in ((ob.OBFrameType.DEPTH_FRAME,'depth'),(ob.OBFrameType.LEFT_IR_FRAME,'ir_left'),(ob.OBFrameType.RIGHT_IR_FRAME,'ir_right'),(ob.OBFrameType.COLOR_FRAME,'color')):
                try:frame=frames.get_frame(kind)
                except Exception:frame=None
                if frame is not None:seen[label]=seen.get(label,0)+1
        print('frames',seen)
    finally:pipeline.stop()

if __name__=='__main__':main()
