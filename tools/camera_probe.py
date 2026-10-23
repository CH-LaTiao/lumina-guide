"""Bounded RGB-D diagnostic; no images retained, uploaded or sent to physical outputs."""
import argparse, json, time, uuid
from pathlib import Path
import numpy as np
import pyorbbecsdk as ob
p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=10);p.add_argument('--output',type=Path,required=True)
p.add_argument('--record',type=Path,help='Explicit opt-in: locally store at most five RGB/depth pairs at 1Hz; never uploads')
a=p.parse_args()
if not 1<=a.seconds<=60:p.error('seconds must be 1..60')
ctx=ob.Context();devices=ctx.query_devices()
if devices.get_count()!=1:raise SystemExit('Require exactly one camera; configure serial selection before multiple-device use')
device=devices.get_device_by_index(0);info=device.get_device_info()
if '336L' not in info.get_name():raise SystemExit('Unexpected camera model')
pipeline=ob.Pipeline(device);config=ob.Config();profiles={}
for name,sensor,fmt in [('depth',ob.OBSensorType.DEPTH_SENSOR,ob.OBFormat.Y16),('color',ob.OBSensorType.COLOR_SENSOR,ob.OBFormat.MJPG)]:
 choices=pipeline.get_stream_profile_list(sensor);selected=None
 for i in range(choices.get_count()):
  v=choices.get_stream_profile_by_index(i).as_video_stream_profile()
  if v.get_format()==fmt and v.get_fps()==30 and v.get_width()==640:
   selected=v;break
 if selected is None:raise SystemExit('No supported 640-wide 30fps '+name+' profile; enumerate and explicitly revise')
 config.enable_stream(selected)
 profiles[name]={'width':selected.get_width(),'height':selected.get_height(),'fps':selected.get_fps(),'format':str(selected.get_format()),'intrinsics':str(selected.get_intrinsic())}
started=False;rows=[];timeouts=0;missing=0;recorder=None;recorded=0;record_next=0.0
session=str(uuid.uuid4())
try:
 if a.record:
  from recording import Recorder
  recorder=Recorder(a.record)
 pipeline.start(config);started=True;start=time.monotonic();deadline=start+a.seconds
 while time.monotonic()<deadline:
  frames=pipeline.wait_for_frames(1000)
  if frames is None:timeouts+=1;continue
  d=frames.get_depth_frame();color=frames.get_color_frame()
  if d is None or color is None:missing+=1;continue
  values=np.frombuffer(d.get_data(),dtype=np.uint16)
  rows.append({'depth_index':d.get_index(),'color_index':color.get_index(),'depth_us':d.get_timestamp_us(),'color_us':color.get_timestamp_us(),'depth_scale_mm':d.get_depth_scale(),'valid_ratio':float(np.count_nonzero(values)/values.size),'host_mono_ns':time.monotonic_ns()})
  now=time.monotonic()
  if recorder and recorded<5 and now>=record_next:
   recorder.append({'sequence':recorded,'session_id':session,'host_mono_ns':rows[-1]['host_mono_ns'],
    'depth_us':d.get_timestamp_us(),'color_us':color.get_timestamp_us(),'width':d.get_width(),'height':d.get_height(),
    'color_width':color.get_width(),'color_height':color.get_height(),'depth_scale_mm':d.get_depth_scale(),
    'depth_format':'Y16_LE','color_format':'MJPG','aligned':False},bytes(d.get_data()),bytes(color.get_data()))
   recorded+=1;record_next=now+1.0
 elapsed=time.monotonic()-start
finally:
 try:
  if started:pipeline.stop()
 finally:
  if recorder:recorder.close()
report={'scope':'real camera diagnostic only; unaligned RGB/depth; no body calibration or obstacle validation','device':info.get_name(),'serial':info.get_serial_number(),'firmware':info.get_firmware_version(),'profiles':profiles,'elapsed_s':elapsed,'paired_frames':len(rows),'pairs_per_s':len(rows)/elapsed,'timeouts':timeouts,'incomplete_frames':missing,'depth_scale_mm':rows[-1]['depth_scale_mm'] if rows else None,'mean_valid_ratio':sum(r['valid_ratio'] for r in rows)/len(rows) if rows else None,'rows':rows}
report['recorded_frames']=recorded
report['timing_note']='Opt-in recording is synchronous diagnostic I/O; not a realtime performance benchmark'
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x') as f:json.dump(report,f,indent=2)
print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
if len(rows)<a.seconds*15:raise SystemExit('Insufficient paired frames')
