"""Local diagnostics, simulated feedback, and explicitly authorized cloud queries."""
import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
import tracemalloc
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from lan_access import AccessPolicy, AccessHandlerMixin, lan_urls

import cv2
import numpy as np
from box_depth import link as link_boxes
from ground_line import GroundLine
from visual_quality import center_range, rgb_quality

# Preview render cadence. The camera runs at 30 pairs/s; rendering depth colour,
# geometry overlay and two JPEG encodes costs ~40-50ms, so 20Hz is the practical
# ceiling on this board while keeping CUDA detection on its own thread.
PREVIEW_INTERVAL_S=0.05


def trim_heap():
    """Return freed glibc arenas to the OS; no-op outside Linux/glibc."""
    if not sys.platform.startswith('linux'):
        return False
    try:
        return bool(ctypes.CDLL('libc.so.6').malloc_trim(0))
    except (OSError,AttributeError):
        return False

def advance_guidance(state,guidance,feedback,voice,detector=None):
    """One decision writer, independent of browser polling, including detector evidence."""
    info=state.read()[0]
    detection=detector.read()[0] if detector else {'status':'DISABLED','objects':[]}
    geometry=state.get_geometry(detection.get('frame_id'))
    if detection.get('objects') and geometry.get('objects'):
        linked=link_boxes(detection['objects'],geometry['objects'],
            detection.get('frame_id'),geometry.get('frame_id'))
        detection=dict(detection,objects=linked)
    info['detection']=detection if info.get('state')=='STREAMING' else {'status':'CAMERA_UNAVAILABLE','objects':[]}
    with guidance.lock:
        result=guidance.update(info)
        with feedback.lock:
            feedback.update(dict(info,guidance=result),guidance_only=True)
            state.set_guidance(result,feedback.read())
        voice.follow(result)
    return result


class State:
    def __init__(self):
        self.lock=threading.Lock();self.jpeg=None;self.stamp=0;self.snapshot=None
        self.geometry_frames={};self.guidance={};self.feedback={};self.guidance_stamp=0
        self.info={'state':'STARTING','object_detection':'not_implemented','aligned':False}
    def update(self, info, jpeg=None, snapshot=None):
        with self.lock:
            self.info=info
            geometry=info.get('geometry') or {}
            if geometry.get('state')=='READY' and snapshot and info.get('frame_id'):
                self.geometry_frames[info['frame_id']]=(snapshot['captured'],geometry)
                while len(self.geometry_frames)>24:self.geometry_frames.pop(next(iter(self.geometry_frames)))
            if info.get('state') not in ('STREAMING','WARMING'):self.geometry_frames.clear()
            self.snapshot=snapshot
            if jpeg is not None:self.jpeg=jpeg;self.stamp=snapshot['captured'] if snapshot else time.monotonic()
            else:self.jpeg=None;self.stamp=0
    def read(self):
        with self.lock:
            age=time.monotonic()-self.stamp if self.stamp else None
            info=dict(self.info,frame_age_ms=round(age*1000) if age is not None else None)
            if self.guidance:info['guidance']=dict(self.guidance)
            if self.feedback:info['feedback']=dict(self.feedback)
            if age is not None and age > .2 and 'geometry' in info:
                info['geometry']=dict(info['geometry'],state='STALE',objects=[],plane=None,ground_state='UNKNOWN')
            if age is not None and age > .2 and 'geometry_display' in info:
                shown=info['geometry_display']
                info['geometry_display']=dict(shown,display_hold=True,
                    display_age_ms=max(shown.get('display_age_ms') or 0,round(age*1000,1)))
            if age is not None and age>0.5:info['state']='STALE'
            if self.guidance and self.guidance.get('mode')!='PAUSED':
                limit=(self.guidance.get('policy') or {}).get('fresh_limit_ms',200)
                expired=(time.monotonic()-self.guidance_stamp>.6 or info['state']!='STREAMING'
                    or age is None or age*1000>limit)
                if expired:
                    walking=self.guidance.get('mode')=='WALKING'
                    cue='停，感知不可用。' if walking else '感知不可用。'
                    info['guidance'].update(fresh=False,action='STOP' if walking else 'UNAVAILABLE',
                        speech=cue,priority=90,candidate=None,direction=None,detour=None,route=None,
                        traffic_decision='STOP',can_continue=False,must_turn=False,corridors=[],
                        summary='',hazard_regions=[],reasons=['STALE_OR_UNAVAILABLE'])
                    if self.feedback:
                        speech=None if self.feedback.get('paused') else dict(
                            self.feedback.get('speech') or {},text=cue,action=info['guidance']['action'],
                            kind='GUIDANCE:'+info['guidance']['action'],direction=None,channels=[],fresh=False,priority=90,
                            guidance_event_id=self.guidance.get('event_id'),frame_id=self.guidance.get('frame_id'))
                        info['feedback'].update(speech=speech,haptics=[{'channel':i,'active':False} for i in range(6)])
            return info,self.jpeg if info['state']=='STREAMING' and age is not None and age<=0.5 else None
    def set_guidance(self,value,feedback=None):
        with self.lock:
            self.guidance=dict(value);self.guidance_stamp=time.monotonic()
            if feedback is not None:self.feedback=dict(feedback)
    def set_feedback(self,value):
        with self.lock:self.feedback=dict(value)
    def peek_jpeg(self):
        """Lightweight read for the MJPEG loop; avoids copying the status dict."""
        with self.lock:
            return self.jpeg,self.stamp,self.info.get('state')
    def get_snapshot(self):
        with self.lock:
            if self.info.get('state') != 'STREAMING' or not self.snapshot or time.monotonic()-self.snapshot['captured'] > .5:return None
            snapshot=dict(self.snapshot)
        rgb=snapshot.pop('rgb',None)
        if 'jpeg' not in snapshot and rgb is not None:
            ok,jpeg=cv2.imencode('.jpg',rgb,[cv2.IMWRITE_JPEG_QUALITY,80])
            if not ok:return None
            snapshot['jpeg']=jpeg.tobytes()
        return snapshot
    def get_geometry(self,frame_id):
        with self.lock:
            item=self.geometry_frames.get(frame_id)
            return item[1] if item and 0<=time.monotonic()-item[0]<=2 else {}

def depth_panel(raw, scale):
    meters=raw.astype(np.float32)*scale/1000.0
    valid=raw>0
    panel=cv2.applyColorMap(np.uint8(np.clip(meters/4,0,1)*255),cv2.COLORMAP_TURBO)
    panel[~valid]=0
    h,w=raw.shape;regions=[]
    for i,name in enumerate(('IMAGE LEFT','IMAGE CENTER','IMAGE RIGHT')):
        x0=i*w//3;x1=(i+1)*w//3;y0=h//4;y1=3*h//4
        roi=meters[y0:y1,x0:x1];mask=valid[y0:y1,x0:x1]
        ratio=float(mask.mean());sample=roi[mask]
        p10=float(np.percentile(sample,10)) if sample.size else None
        status='UNKNOWN' if ratio<0.6 else 'DEPTH ONLY'
        color=(0,190,255) if ratio<0.6 else (80,230,100)
        cv2.rectangle(panel,(x0+2,y0),(x1-2,y1),color,2)
        label='unknown' if p10 is None else f'p10 {p10:.2f}m'
        for j,line in enumerate((name,label,f'valid {ratio:.0%}',status)):
            cv2.putText(panel,line,(x0+6,y0+22+j*23),cv2.FONT_HERSHEY_SIMPLEX,.43,(255,255,255),1,cv2.LINE_AA)
        regions.append({'region':name,'valid_ratio':ratio,'depth_p10_m':p10,'status':status})
    return panel,regions,float(valid.mean())

class Latest:
    """Single-slot handoff: the renderer always gets the newest frame."""
    def __init__(self):
        self.lock=threading.Lock();self.item=None;self.event=threading.Event()
    def put(self,item):
        with self.lock:self.item=item
        self.event.set()
    def take(self):
        with self.lock:
            item=self.item;self.item=None
            if item is None:self.event.clear()
            return item


def render_loop(draw,state,stop,reference,body_calibration,serial,device_name,laser,session_id,ground_line=None):
    from guidance import SceneAnalysis, overlay as guidance_overlay
    analysis=SceneAnalysis()
    from geometry import Geometry
    from native_depth import NativeDepth
    native=NativeDepth();geometry=None;geometry_error=None;rendered=0
    geometry_good=None;geometry_good_at=0.;geometry_display=None;last_line_key=None
    last_trim=time.monotonic()
    resized=np.empty((480,640,3),np.uint8)
    composite=np.empty((480,1280,3),np.uint8)
    while not stop.is_set():
        item=draw.take()
        if item is None:
            draw.event.wait(.02);continue
        started=time.monotonic();now=item['captured'];rgb=item['rgb'];raw=item['raw'];scale=item['scale'];frame_id=item['frame_id']
        imu=item.get('imu') or {'state':'UNAVAILABLE','source':'CAMERA_IMU_NOT_LOCALIZATION'}
        try:
            quality=rgb_quality(rgb)
            line_info=(ground_line.read(session_id,item['model']['sha256'],item['model']['height'])
                if ground_line else {'state':'DEFAULT','active':False,'line_y':item['model']['height']*.55})
            line_y=line_info.get('line_y')
            if line_y!=last_line_key:
                geometry_good=None;geometry_good_at=0.;last_line_key=line_y
            geometry_info={'state':'UNAVAILABLE','objects':[],'ground_state':'UNKNOWN','movement_allowed':False}
            try:
                if geometry is None and geometry_error is None:
                    geometry=Geometry(item['model'],native,reference,body_calibration)
                if geometry is not None:
                    geometry_info,mask=geometry.evaluate(raw,scale,(time.monotonic()-now)*1000,frame_id,session_id,
                        ground_line_y=line_y)
                    rgb=geometry.overlay(rgb,geometry_info,mask)
            except (ValueError,AttributeError,RuntimeError) as exc:
                geometry_error=str(exc);geometry=None
            if geometry_error:geometry_info['error']=geometry_error
            geometry_line={**line_info,**(geometry_info.get('ground_line') or {})}
            geometry_info['ground_line']=geometry_line
            render_clock=time.monotonic()
            if geometry_info.get('state')=='READY':
                geometry_good=dict(geometry_info);geometry_good_at=render_clock
                geometry_display=dict(geometry_good,display_hold=False)
            elif geometry_good is not None and render_clock-geometry_good_at<=1.:
                geometry_display=dict(geometry_good,display_hold=True,
                    display_age_ms=round((render_clock-geometry_good_at)*1000,1))
            else:
                geometry_display=dict(geometry_info,display_hold=False)
            if geometry_error:geometry_display['error']=geometry_error
            geometry_display['ground_line']=geometry_line
            calibration=body_calibration.read(session_id,item['model']['sha256'])
            geometry_info['body_calibration']=calibration or {'state':'UNSET'}
            geometry_display['body_calibration']=calibration or {'state':'UNSET'}
            guide_started=time.monotonic()
            guide_analysis=analysis.evaluate(item['rgb'],raw,scale,item['model'],geometry_info,now,
                session_id,calibration,item['hazards'])
            guide_ms=(time.monotonic()-guide_started)*1000
            body_mount_ground=(calibration is not None
                and geometry_info.get('ground_state')=='REFERENCE_MATCHED_CANDIDATE')
            if guide_analysis['motion_hint']['state']=='CAMERA_MOVING' and not body_mount_ground:
                reference.set({'clear':True},{})
                guide_analysis['corridors']=[]
                guide_analysis['fine']=[]
                geometry_info=dict(geometry_info,ground_state='REFERENCE_INVALIDATED_MOTION',reference=None)
                geometry_display=dict(geometry_info,body_calibration=geometry_display.get('body_calibration'),
                    display_hold=False)
            elif guide_analysis['motion_hint']['state']=='CAMERA_MOVING':
                geometry_info['motion_policy']='BODY_MOUNT_GROUND_TRACKING'
            depth,regions,coverage=depth_panel(raw,scale)
            range_center=center_range(raw,scale)
            rgb=guidance_overlay(rgb,guide_analysis)
            rgb=cv2.resize(rgb,(640,480),dst=resized)
            composite[:, :640]=rgb;composite[:, 640:]=depth
            image=composite
            cv2.rectangle(image,(0,0),(1280,62),(20,25,30),-1)
            cv2.putText(image,'GEOMETRY | G boxes: 3D clusters / dots: plane',(12,25),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
            cv2.putText(image,'DEPTH ROI | NOT object boxes',(650,25),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),1)
            cv2.putText(image,f"{geometry_info['ground_state']} / {frame_id}",(12,50),cv2.FONT_HERSHEY_SIMPLEX,.52,(80,220,255),1)
            ok,jpg=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,80])
            if not ok:raise RuntimeError('Preview encode failed')
            rendered+=1
            if time.monotonic()-last_trim>=60:
                trim_heap();last_trim=time.monotonic()
            state.update({'state':'STREAMING' if rendered>=10 else 'WARMING','device':device_name,'serial':serial,'paired_frames':item['count'],
                'camera_fps':item['camera_fps'],'range_center_m':range_center,'rgb_quality':quality,'laser':laser,
                'session_id':session_id,'frame_id':frame_id,'warmup_remaining':max(0,10-rendered),
                'capture_pairs_per_s':item['rate'],'rate_window_s':5,'preview_frames':rendered,'valid_depth_ratio':coverage,
                'align_ms':round(item['align_ms'],1),'render_ms':round((time.monotonic()-now)*1000,1),
                'guidance_ms':round(guide_ms,1),
                'regions':regions,'aligned':True,'object_detection':'separate_snapshot','native_depth':item['hazards'],
                'native_depth_ms':item['native_ms'],'hazard_scope':'image ROI diagnostic; geometry is separate and does not drive feedback',
                'geometry':geometry_info,'geometry_display':geometry_display,'ground_line':geometry_line,
                'guidance_analysis':guide_analysis,'imu':imu,
                'real_output':False,'depth_us':item['depth_us'],'color_us':item['color_us']},
                jpg.tobytes(),{'rgb':item['rgb'],'captured':now,'frame_id':frame_id})
        except Exception as exc:
            state.update({'state':'FAULT','device':device_name,'serial':serial,
                'error':type(exc).__name__+': '+str(exc)[:120],'real_output':False},None)
        elapsed=time.monotonic()-started
        if elapsed<PREVIEW_INTERVAL_S:time.sleep(PREVIEW_INTERVAL_S-elapsed)


def capture_shm(state,stop,serial,detector=None,reference=None,body_calibration=None,ground_line=None):
    """Consume frames published by core/frame_server.cpp instead of the SDK."""
    from frame_shm import FrameShm
    from imu_shm import ImuShm
    from gait import GaitDetector
    from body_calibration import BodyCalibration
    from geometry import BenchReference, canonical_hash
    from native_depth import NativeDepth
    reader=FrameShm();native=NativeDepth();reference=reference or BenchReference()
    body_calibration=body_calibration or BodyCalibration()
    imu_reader=None;imu_thread=None;imu_stop=threading.Event();imu_lock=threading.Lock()
    imu_latest={'state':'UNAVAILABLE','source':'CAMERA_IMU_NOT_LOCALIZATION'}
    try:
        imu_reader=ImuShm()
        from runtime_paths import runtime_dir
        profile_path=runtime_dir()/'gait-calibration.json'
        gait=GaitDetector(profile_path=profile_path)
        def imu_loop():
            last_seq=0
            while not imu_stop.is_set() and not stop.is_set():
                value=imu_reader.latest()
                if value and value['seq']!=last_seq:
                    last_seq=value['seq'];gait.update(value)
                    current=gait.read()
                    with imu_lock:imu_latest.clear();imu_latest.update(current)
                time.sleep(.01)
        imu_thread=threading.Thread(target=imu_loop,daemon=True);imu_thread.start()
    except (OSError,ValueError):
        imu_reader=None
    def read_imu():
        with imu_lock:return dict(imu_latest)
    intr=reader.intrinsics
    model={'width':int(intr['width']),'height':int(intr['height']),'fx':float(intr['fx']),
        'fy':float(intr['fy']),'cx':float(intr['cx']),'cy':float(intr['cy']),
        'distortion_model':'NONE','distortion':[0.0]*8,'serial':serial}
    model['sha256']=canonical_hash(model)
    session_id=f'{reader.epoch:x}-{uuid.uuid4().hex[:6]}'
    draw=Latest();render_stop=threading.Event()
    threading.Thread(target=render_loop,args=(draw,state,render_stop,reference,body_calibration,serial,
        'Orbbec Gemini 336L (shared memory)',{'on':None,'power':None},session_id,ground_line),daemon=True).start()
    recent=deque(maxlen=300);count=0;last_seq=0;first_index=None;index_started=time.monotonic()
    last_frame=None;started=time.monotonic()
    try:
        while not stop.is_set() and not render_stop.is_set():
            if time.monotonic()-(last_frame if last_frame is not None else started)>(2 if last_frame is not None else 10):
                raise RuntimeError('Shared-memory sequence stopped advancing')
            frame=reader.latest()
            if frame is None:
                time.sleep(.005);continue
            meta,depth_bytes,color_bytes=frame
            if meta['seq']==last_seq:
                time.sleep(.005);continue
            last_seq=meta['seq'];observed=time.monotonic();now=meta['received_ns']/1e9
            if (not 0<=observed-now<=.2 or meta['published_ns']/1e9>observed
                    or abs(meta['depth_ts_us']-meta['color_ts_us'])>50000):
                continue
            last_frame=observed
            raw=np.frombuffer(depth_bytes,np.uint16).reshape(meta['height'],meta['width']).copy()
            rgb=cv2.imdecode(np.frombuffer(color_bytes,np.uint8),cv2.IMREAD_COLOR)
            if rgb is None:continue
            if raw.shape!=rgb.shape[:2]:raise RuntimeError('RGB-D shape mismatch')
            count+=1;recent.append((now,count))
            while recent and now-recent[0][0]>5:recent.popleft()
            rate=(count-recent[0][1])/max(1e-6,now-recent[0][0]) if len(recent)>1 else 0.0
            if first_index is None:first_index=meta['frame_id'];index_started=now
            camera_fps=(round((meta['frame_id']-first_index)/max(1e-6,now-index_started),1)
                if meta['frame_id']>=first_index else None)
            frame_id=f"{session_id}:{meta['frame_id']}"
            hazards=native.evaluate(raw,meta['depth_scale'],(time.monotonic()-now)*1000)
            if detector and count>=10:detector.submit(rgb,raw,meta['depth_scale'],frame_id,now)
            draw.put({'rgb':rgb,'raw':raw,'scale':meta['depth_scale'],'frame_id':frame_id,'captured':now,
                'hazards':hazards,'native_ms':0.0,'count':count,'model':model,'align_ms':(meta['published_ns']-meta['received_ns'])/1e6,
                'camera_fps':camera_fps,'rate':round(rate,1),
                'depth_us':meta['depth_ts_us'],'color_us':meta['color_ts_us'],'imu':read_imu()})
    finally:
        render_stop.set();imu_stop.set()
        if imu_thread is not None:imu_thread.join(.5)
        if imu_reader is not None:imu_reader.close()
        reader.close()


def capture(state,stop,serial,detector=None,reference=None,body_calibration=None,ground_line=None):
    if os.environ.get('LUMINA_FRAME_SOURCE','sdk')=='shm':
        try:return capture_shm(state,stop,serial,detector,reference,body_calibration,ground_line)
        except Exception:
            if not stop.is_set():
                command=[sys.executable,str(Path(__file__).with_name('preview_control.py'))]
                for action in ('frame-stop','frame-start'):
                    try:subprocess.run(command+[action],timeout=8,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    except (OSError,subprocess.SubprocessError):pass
            raise
    import pyorbbecsdk as ob
    pipeline=None;started=False;render_stop=threading.Event()
    try:
        from native_depth import NativeDepth
        from body_calibration import BodyCalibration
        from geometry import Geometry, camera_model, BenchReference
        native=NativeDepth()
        reference=reference or BenchReference();body_calibration=body_calibration or BodyCalibration()
        geometry=None;geometry_error=None
        ctx=ob.Context();devices=ctx.query_devices();device=None
        for i in range(devices.get_count()):
            candidate=devices.get_device_by_index(i)
            if candidate.get_device_info().get_serial_number()==serial:device=candidate;break
        if device is None:raise RuntimeError('Configured camera not found')
        info=device.get_device_info();pipeline=ob.Pipeline(device);config=ob.Config()
        laser={'on':None,'power':None}
        try:
            laser={'on':int(device.get_int_property(ob.OBPropertyID.OB_PROP_LASER_CONTROL_INT)),
                'power':int(device.get_int_property(ob.OBPropertyID.OB_PROP_LASER_POWER_ACTUAL_LEVEL_INT))}
        except Exception:pass
        for sensor,fmt in ((ob.OBSensorType.DEPTH_SENSOR,ob.OBFormat.Y16),(ob.OBSensorType.COLOR_SENSOR,ob.OBFormat.MJPG)):
            profiles=pipeline.get_stream_profile_list(sensor);chosen=None
            for i in range(profiles.get_count()):
                v=profiles.get_stream_profile_by_index(i).as_video_stream_profile()
                if v.get_width()==640 and v.get_height()==480 and v.get_fps()==30 and v.get_format()==fmt:
                    chosen=v;break
            if chosen is None:raise RuntimeError('Required 640x480@30 profile unavailable')
            config.enable_stream(chosen)
        align=ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
        pipeline.start(config);started=True;start=time.monotonic();count=0
        index_start=None;index_started=start
        recent=deque(maxlen=300)
        last_pair=start;session_id=uuid.uuid4().hex[:12]
        draw=Latest()
        threading.Thread(target=render_loop,args=(draw,state,render_stop,reference,body_calibration,
            serial,info.get_name(),laser,session_id,ground_line),daemon=True).start()
        model=None
        while not stop.is_set():
            frames=pipeline.wait_for_frames(500)
            if time.monotonic()-last_pair>2:raise RuntimeError('No complete RGB-D pair for 2 seconds')
            if frames is None:continue
            d=frames.get_depth_frame();c=frames.get_color_frame()
            if d is None or c is None:continue
            count+=1;now=time.monotonic();last_pair=now
            recent.append((now,count))
            while recent and now-recent[0][0]>5:recent.popleft()
            window_rate=(count-recent[0][1])/max(1e-6,now-recent[0][0]) if len(recent)>1 else 0.0
            index=c.get_index()
            if index_start is None:index_start=index;index_started=now
            align_started=time.monotonic()
            aligned=align.process(frames)
            align_ms=(time.monotonic()-align_started)*1000
            if aligned is None:raise RuntimeError('Depth alignment returned no frame')
            aligned=aligned.as_frame_set();d=aligned.get_depth_frame();c=aligned.get_color_frame()
            if d is None or c is None:raise RuntimeError('Aligned RGB-D pair incomplete')
            rgb=cv2.imdecode(np.frombuffer(c.get_data(),np.uint8),cv2.IMREAD_COLOR)
            if rgb is None:raise RuntimeError('Color JPEG decode failed')
            raw=np.frombuffer(d.get_data(),np.uint16).reshape(d.get_height(),d.get_width()).copy()
            if raw.shape!=rgb.shape[:2]:raise RuntimeError('Aligned depth size mismatch')
            if abs(d.get_timestamp_us()-c.get_timestamp_us())>50000:raise RuntimeError('RGB-D timestamps exceed 50ms tolerance')
            if model is None:
                model=camera_model(d.get_stream_profile().as_video_stream_profile(),serial)
                color_model=camera_model(c.get_stream_profile().as_video_stream_profile(),serial)
                if any(abs(model[k]-color_model[k])>1e-3 for k in ('width','height','fx','fy','cx','cy')):
                    raise ValueError('Aligned depth/color intrinsic mismatch')
            native_started=time.monotonic()
            hazards=native.evaluate(raw,d.get_depth_scale(),(time.monotonic()-now)*1000)
            native_ms=(time.monotonic()-native_started)*1000
            frame_id=f'{session_id}:{int(c.get_index())}'
            if detector and count>=10:detector.submit(rgb,raw,d.get_depth_scale(),frame_id,now)
            draw.put({'rgb':rgb,'raw':raw,'scale':d.get_depth_scale(),'frame_id':frame_id,'captured':now,
                'hazards':hazards,'native_ms':native_ms,'count':count,'model':model,'align_ms':align_ms,
                'camera_fps':round((index-index_start)/max(1e-6,now-index_started),1) if index>=index_start else None,
                'rate':round(window_rate,1),'depth_us':d.get_timestamp_us(),'color_us':c.get_timestamp_us()})
    finally:
        render_stop.set()
        if started:
            try:pipeline.stop()
            except Exception:pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);p.add_argument('--serial',required=True);p.add_argument('--model',type=Path)
    p.add_argument('--host',choices=['127.0.0.1','0.0.0.0'],default='127.0.0.1');a=p.parse_args()
    if os.environ.get('LUMINA_TRACEMALLOC'):
        tracemalloc.start(int(os.environ['LUMINA_TRACEMALLOC']))
    detector=None
    if a.model:
        from detection import Detector
        detector=Detector(a.model)
    state=State();stop=threading.Event();page=(Path(__file__).parent/'live_view.html').read_bytes()
    from online_query import OnlineQuery
    from auto_help import AutoHelp,safe_auto_speech
    from feedback_sim import FeedbackSimulator
    from review_store import ReviewStore
    from camera_recovery import supervise_capture
    from body_calibration import BodyCalibration
    from geometry import BenchReference
    from guidance import Guidance
    from readiness import assess
    from software_version import identity
    from voice_io import VoiceIO, answer_text
    from gait_calibration import GaitCalibrationManager
    from runtime_paths import runtime_dir
    guidance=Guidance();software=identity()
    voice=VoiceIO()
    gait_calibration=GaitCalibrationManager(runtime_dir()/'gait-calibration.json',
        notify=lambda text:voice.say(text,60))
    reference=BenchReference()
    body_calibration=BodyCalibration()
    ground_line=GroundLine()
    online=OnlineQuery()
    auto_help=AutoHelp();auto_spoken=None
    feedback=FeedbackSimulator();reviews=ReviewStore(online.db_path)
    access=AccessPolicy(a.port,lan=a.host=='0.0.0.0')
    network={'lan':access.lan,'urls':lan_urls(a.port) if access.lan else [],'access_code_required':False}
    class Handler(AccessHandlerMixin,BaseHTTPRequestHandler):
        access_policy=access
        def do_GET(self):
            if not self.access_guard():return
            path=self.path.split('?')[0];info,jpg=state.read()
            if path=='/stream.mjpg':
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                previous=None
                try:
                    while not stop.is_set():
                        frame,stamp,stream_state=state.peek_jpeg()
                        if stream_state!='STREAMING' or not stamp or time.monotonic()-stamp>0.5:
                            time.sleep(.05);continue
                        if frame is None or frame is previous:time.sleep(.005);continue
                        previous=frame
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(frame)).encode()+b'\r\n\r\n'+frame+b'\r\n')
                except (BrokenPipeError,ConnectionResetError,OSError):pass
                return
            detection,detection_jpg=detector.read() if detector else ({'status':'DISABLED','objects':[]},None)
            geometry=state.get_geometry(detection.get('frame_id'))
            if detection.get('objects') and geometry.get('objects'):
                linked=link_boxes(detection['objects'],geometry['objects'],
                    detection.get('frame_id'),geometry.get('frame_id'))
                detection=dict(detection,objects=linked,
                    cluster_linked=sum(1 for obj in linked if obj.get('depth_source')=='cluster'))
            requested_frame=parse_qs(urlsplit(self.path).query).get('frame_id',[None])[0]
            detection_age=None
            if path=='/detection.jpg':
                detection_jpg,detection_age=detector.read_frame(requested_frame) if detector else (None,None)
            if info['state']!='STREAMING':detection={'status':'CAMERA_UNAVAILABLE','objects':[]};detection_jpg=None
            info['detection']=detection
            info['online']=online.read()
            info['auto_help']=auto_help.read()
            info['gait_calibration']=gait_calibration.read()
            info.setdefault('feedback',feedback.read())
            info.setdefault('guidance',{})
            info['audio']=voice.read()
            info['readiness']=assess(info)
            info['software_version']=software['version'];info['software']=software
            info['network']=network
            if path=='/':content=page;ctype='text/html; charset=utf-8';code=200
            elif path=='/debug/memory' and tracemalloc.is_tracing():
                snapshot=tracemalloc.take_snapshot();current,peak=tracemalloc.get_traced_memory()
                payload={'current_bytes':current,'peak_bytes':peak,
                    'top':[{'file':stat.traceback[0].filename,'line':stat.traceback[0].lineno,
                        'size':stat.size,'count':stat.count} for stat in snapshot.statistics('lineno')[:12]]}
                content=json.dumps(payload,ensure_ascii=False).encode();ctype='application/json; charset=utf-8';code=200
            elif path=='/live_view.js':content=(Path(__file__).parent/'live_view.js').read_bytes();ctype='text/javascript; charset=utf-8';code=200
            elif path=='/prompt_ui.js':content=(Path(__file__).parent/'prompt_ui.js').read_bytes();ctype='text/javascript; charset=utf-8';code=200
            elif path=='/status':content=json.dumps(info).encode();ctype='application/json';code=200
            elif path=='/reviews.csv':content=reviews.export_csv();ctype='text/csv; charset=utf-8';code=200
            elif path=='/reviews/status':content=json.dumps(reviews.summary()).encode();ctype='application/json';code=200
            elif path=='/reviews/candidates':content=json.dumps(reviews.candidates(),ensure_ascii=False).encode();ctype='application/json; charset=utf-8';code=200
            elif path=='/prompts/status':content=json.dumps(online.prompts.status(),ensure_ascii=False).encode();ctype='application/json; charset=utf-8';code=200
            elif path=='/prompts/report.csv':content=online.prompts.export_report();ctype='text/csv; charset=utf-8';code=200
            elif path=='/frame.jpg' and jpg is not None:content=jpg;ctype='image/jpeg';code=200
            elif path=='/detection.jpg' and detection_jpg is not None:content=detection_jpg;ctype='image/jpeg';code=200
            else:content=b'No fresh frame' if path in ('/frame.jpg','/detection.jpg') else b'Not found';ctype='text/plain';code=503 if path in ('/frame.jpg','/detection.jpg') else 404
            self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(content)))
            if path=='/detection.jpg' and code==200:
                self.send_header('X-Lumina-Frame-Id',requested_frame)
                self.send_header('X-Lumina-Frame-Age-Ms',str(round(detection_age)))
            if path in ('/reviews.csv','/prompts/report.csv'):self.send_header('Content-Disposition','attachment; filename="'+('lumina-reviews.csv' if path=='/reviews.csv' else 'prompt-trials.csv')+'"')
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
            try:self.wfile.write(content)
            except (BrokenPipeError,ConnectionResetError):pass
        def log_message(self,*args):pass
        def do_POST(self):
            if not self.access_guard():return
            try:
                size=int(self.headers.get('Content-Length','0'))
                limit=2*1024*1024 if self.path=='/reviews/import' else (16384 if self.path.startswith('/prompts/') else 4096)
                if not 0<size<=limit:raise ValueError('Invalid request size')
                self.connection.settimeout(3)
                body=self.rfile.read(size)
                if len(body)!=size:raise ValueError('Incomplete request')
                payload={} if self.path=='/reviews/import' else json.loads(body)
                if not isinstance(payload,dict):raise ValueError('JSON object required')
                if self.path=='/reviews/import':result=reviews.import_csv(body);code=200
                elif self.path=='/prompts/sync':result=online.prompts.sync_candidates();code=200
                elif self.path=='/prompts/trial':
                    if guidance.mode!='STANDING':raise ValueError('请先站立观察，再试答提示词')
                    version=online.prompts.version(payload.get('version'))
                    rid=online.submit(payload.get('question'),version['scene'],state.get_snapshot(),payload.get('consent'),True,version['version'])
                    result={'request_id':rid};code=202
                elif self.path=='/prompts/assess':
                    result=online.prompts.assess_trial(payload.get('request_id'),payload.get('accepted'),payload.get('checks'),payload.get('note'));code=200
                elif self.path in ('/prompts/activate','/prompts/rollback'):
                    result=online.prompts.activate(payload.get('scene'),payload.get('version'),payload.get('generation'),payload.get('note'),rollback=self.path.endswith('/rollback'))
                    online.reset_task();code=200
                elif self.path=='/feedback/pause':
                    with feedback.lock:
                        feedback.pause(payload.get('paused'));result=feedback.read();state.set_feedback(result);code=200
                elif self.path=='/geometry/reference':result=reference.set(payload,state.read()[0]);code=200
                elif self.path=='/ground-line':result=ground_line.set(payload,state.read()[0]);code=200
                elif self.path=='/body/calibration':result=body_calibration.set(payload,state.read()[0]);code=200
                elif self.path=='/guidance/mode':
                    with guidance.lock:
                        result=guidance.set_mode(payload.get('mode'));voice.cancel();online.cancel();code=200
                elif self.path=='/audio/auto':result=voice.set_auto(payload.get('enabled'));code=200
                elif self.path=='/audio/test':voice.say('灵犀语音测试。现在是桌面测试模式。');result={'state':'QUEUED'};code=202
                elif self.path=='/audio/record':
                    if guidance.mode!='STANDING':raise ValueError('请先站立，再录音提问')
                    result=voice.record(payload.get('consent'));code=202
                elif self.path=='/audio/cancel':result=voice.cancel();code=200
                elif self.path=='/audio/answer':
                    voice.say(answer_text(online.read(),guidance.mode));result={'state':'QUEUED'};code=202
                elif self.path=='/query':
                    if guidance.mode=='WALKING':raise ValueError('请先切换站立观察，再进行详细场景问答')
                    rid=online.submit(payload.get('question'),payload.get('scene'),state.get_snapshot(),payload.get('consent'),payload.get('review') is True)
                    result={'request_id':rid};code=202
                elif self.path=='/online/auto_help':
                    result=auto_help.configure(payload.get('enabled') is True,payload.get('consent') is True);code=200
                elif self.path=='/imu/gait_calibration':
                    action=payload.get('action')
                    if action=='start_still':
                        result=gait_calibration.start_still(payload.get('consent') is True,
                            payload.get('safe_environment') is True);code=202
                    elif action=='start_walk':
                        result=gait_calibration.start_walk(payload.get('consent') is True,
                            payload.get('safe_environment') is True);code=202
                    elif action=='reset':result=gait_calibration.reset();code=200
                    else:raise ValueError('未知步态标定操作')
                elif self.path=='/cancel':online.cancel();result={'state':'CANCELLED'};code=200
                elif self.path=='/task/reset':online.reset_task();result={'task':'RESET'};code=200
                else:self.send_error(404);return
            except (ValueError,TypeError) as exc:
                result={'error':str(exc)};code=400
            data=json.dumps(result,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer((a.host,a.port),Handler);server.timeout=.5
    # Remove only our obsolete pairing file when upgrading from v0.8.8.
    (runtime_dir()/'lan-access.json').unlink(missing_ok=True)
    def shutdown(*_):stop.set()
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    def capture_with_context(s,event,serial,d):return capture(s,event,serial,d,reference,body_calibration,ground_line)
    worker=threading.Thread(target=supervise_capture,args=(capture_with_context,state,stop,a.serial,detector,online.invalidate_camera),daemon=True);worker.start()
    def guidance_loop():
        while not stop.is_set():
            advance_guidance(state,guidance,feedback,voice,detector)
            stop.wait(.05)
    guidance_worker=threading.Thread(target=guidance_loop,daemon=True);guidance_worker.start()
    def auto_help_loop():
        nonlocal auto_spoken
        while not stop.is_set():
            info=state.read()[0];info['online']=online.read();info['readiness']=assess(info)
            event=auto_help.evaluate(info)
            if event:
                snapshot=state.get_snapshot()
                if snapshot is not None:
                    try:
                        rid=online.submit(event['question'],event['scene'],snapshot,True,True)
                        auto_help.note_submitted(rid)
                    except ValueError:pass
            result=online.read();rid=result.get('request_id')
            if (auto_help.enabled and rid and rid==auto_help.last_request_id
                    and rid!=auto_spoken):
                text=safe_auto_speech(result)
                if text:
                    try:
                        if voice.say(text,25):auto_spoken=rid
                    except ValueError:pass
            stop.wait(.5)
    auto_worker=threading.Thread(target=auto_help_loop,daemon=True);auto_worker.start()
    print(f'Preview http://127.0.0.1:{a.port} | LAN enabled: {access.lan}',flush=True)
    try:
        while not stop.is_set():server.handle_request()
    finally:
        stop.set();online.cancel();guidance_worker.join(timeout=1);voice.close();worker.join(timeout=3)
        auto_worker.join(timeout=1);server.server_close()
        if detector:detector.stop.set();detector.thread.join(timeout=3)
if __name__=='__main__':main()
