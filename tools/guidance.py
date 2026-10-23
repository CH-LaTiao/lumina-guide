"""Standing/walking interaction policy and camera-space corridor diagnostics.

Modes express user intent, not inferred gait. Camera flow is a mounting-change
hint only. Bench geometry NEVER grants body-relative movement permission.
"""
import math
import statistics
import threading
import time
from collections import deque
import cv2
import numpy as np
from body_calibration import map_native_regions, to_body_axes
from geometry import make_rays

LABELS={'LEFT':'左侧','CENTER':'前方','RIGHT':'右侧'}
LABEL_ZH={'person':'行人','chair':'椅子','couch':'沙发','bed':'床','dining table':'桌子',
    'tv':'显示器','laptop':'笔记本电脑','bottle':'瓶子','backpack':'背包','handbag':'包',
    'suitcase':'行李箱','umbrella':'伞','car':'汽车','bicycle':'自行车','motorcycle':'摩托车',
    'traffic light':'红绿灯','stop sign':'停止标志','door':'门','bench':'长椅','potted plant':'盆栽'}


def nearest_object(info,direction,limit=1.5,session=None,max_age_ms=500):
    """A fresh, same-session labelled detection in the near image third."""
    detection=info.get('detection') or {}
    age=detection.get('age_ms');frame=detection.get('frame_id')
    if (detection.get('status')!='READY' or type(age) not in (int,float)
            or not math.isfinite(age) or not 0<=age<=max_age_ms
            or not isinstance(frame,str)
            or (session is not None and not frame.startswith(str(session)+':'))):
        return None
    objects=detection.get('objects') or []
    best=None
    for obj in objects:
        box=obj.get('bbox') or [0,0,0,0]
        centre=(box[0]+box[2])/2
        side='LEFT' if centre<640/3 else ('RIGHT' if centre>1280/3 else 'CENTER')
        if side!=direction:continue
        distance=obj.get('cluster_distance_m')
        if distance is None:distance=obj.get('depth_patch_m')
        if distance is None or distance>limit:continue
        if best is None or distance<best[0]:best=(distance,obj)
    return best[1] if best else None


def cross_modal_near_hazards(info,session,limit=.9,body_transform=None,body_frame=False):
    """Fresh near-object detector evidence mapped to left/centre/right."""
    if body_frame and not body_transform:return {}
    detection=info.get('detection') or {}
    age=detection.get('age_ms')
    frame=detection.get('frame_id')
    if (detection.get('status')!='READY' or type(age) not in (int,float)
            or not math.isfinite(age) or not 0<=age<=250
            or not isinstance(frame,str) or not frame.startswith(str(session)+':')):
        return {}
    best={}
    for obj in detection.get('objects') or []:
        box=obj.get('bbox') or [0,0,0,0]
        x1,y1,x2,y2=box;centre=(x1+x2)/2
        distance=obj.get('cluster_distance_m')
        if distance is None:distance=obj.get('depth_patch_m')
        if type(distance) not in (int,float) or not math.isfinite(distance) or distance>limit:continue
        body_angle=None
        if body_transform:
            yaw=math.radians(body_transform['yaw_deg'])
            x_cam=(centre-body_transform['cx'])/body_transform['fx']*distance
            body_x=x_cam*math.cos(yaw)+distance*math.sin(yaw)+body_transform['camera_right_m']
            body_z=-x_cam*math.sin(yaw)+distance*math.cos(yaw)+body_transform['camera_forward_m']
            if body_z<=.05:continue
            body_angle=math.degrees(math.atan2(body_x,body_z))
            if abs(body_angle)>30:continue
            direction=coarse_direction(body_angle)
        else:
            direction=('LEFT' if centre<640/3 else ('RIGHT' if centre>1280/3 else 'CENTER'))
        previous=best.get(direction)
        if previous is None or distance<previous['distance_m']:
            best[direction]={'distance_m':float(distance),'label':obj.get('label',''),'score':obj.get('score'),
                  'frame_id':frame,'detection_age_ms':round(age,1),
                  'direction':direction,
                  'direction_frame':'BODY' if body_transform else 'CAMERA',
                  'body_angle_deg':round(body_angle,1) if body_angle is not None else None}
    return best


def cross_modal_near_hazard(info,session,limit=.9,body_transform=None,body_frame=False):
    """Compatibility view: only the centre hazard drives stop decisions."""
    return cross_modal_near_hazards(info,session,limit,body_transform,body_frame).get('CENTER')


class CameraMotion:
    def __init__(self):
        self.previous=None;self.stamp=None;self.session=None

    def update(self,rgb,stamp,session):
        gray=cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_BGR2GRAY),(160,120))
        previous=self.previous;dt=stamp-self.stamp if self.stamp is not None else 0
        reset=session!=self.session or not .015<=dt<=.5
        self.previous=gray;self.stamp=stamp;self.session=session
        if previous is None or reset:return {'state':'UNKNOWN','source':'camera_flow_not_gait'}
        corners=cv2.goodFeaturesToTrack(previous,80,.02,8)
        if corners is None or len(corners)<15:return {'state':'UNKNOWN','source':'camera_flow_not_gait'}
        tracked,status,_=cv2.calcOpticalFlowPyrLK(previous,gray,corners,None,winSize=(15,15),maxLevel=2)
        if tracked is None:return {'state':'UNKNOWN','source':'camera_flow_not_gait'}
        valid=status.ravel().astype(bool)
        if valid.sum()<15:return {'state':'UNKNOWN','source':'camera_flow_not_gait'}
        delta=np.linalg.norm((tracked-corners).reshape(-1,2)[valid],axis=1)
        speed=float(np.median(delta))/dt
        return {'state':'CAMERA_MOVING' if speed>12 else 'CAMERA_STILL',
                'flow_px_s':round(speed,1),'source':'camera_flow_not_gait'}


CLEAR_STATES=('OBSERVED_CLEAR_CANDIDATE','CLEAR_NO_GROUND_EVIDENCE')
FINE_ANGLES=(-30,-20,-10,0,10,20,30)
MIN_PASSAGE_WIDTH_M=.35
COMFORTABLE_WIDTH_M=.7
ROUTE_CONFIRM_FRAMES=4
ROUTE_SWITCH_MARGIN=20
CENTER_RECENTER_MIN_WIDTH_M=.6
TREND_MAX_AGE_S=1.5
DYNAMIC_CLEARANCE_M=.9
DYNAMIC_MIN_SPEED_M_S=.35
DYNAMIC_TTC_S=1.2
URGENT_REPEAT_S=6
ROUTE_REPEAT_S=8
SUMMARY_DETECTION_MAX_AGE_MS=1000


class Trend:
    """Short, time-bounded samples with a split-window change test."""
    def __init__(self,threshold,max_age_s=TREND_MAX_AGE_S):
        self.threshold=threshold;self.max_age_s=max_age_s;self.samples=deque(maxlen=4)

    def clear(self):
        self.samples.clear()

    def add(self,stamp,value):
        self.samples.append((float(stamp),float(value)))
        while self.samples and stamp-self.samples[0][0]>self.max_age_s:self.samples.popleft()

    def dropped(self):
        change=self.change()
        return bool(change and change['drop']>=self.threshold)

    def change(self):
        if len(self.samples)<4:return None
        if self.samples[-1][0]-self.samples[0][0]>self.max_age_s:return None
        samples=list(self.samples);middle=len(samples)//2
        older=statistics.median(value for _,value in samples[:middle])
        recent=statistics.median(value for _,value in samples[middle:])
        older_time=statistics.median(stamp for stamp,_ in samples[:middle])
        recent_time=statistics.median(stamp for stamp,_ in samples[middle:])
        elapsed=recent_time-older_time
        if elapsed<=.05:return None
        return {'drop':older-recent,'speed':max(0.,(older-recent)/elapsed),
                'recent_m':recent,'span_s':samples[-1][0]-samples[0][0]}


def route_rank(corridor):
    """Rank passable sectors: confirmed ground, useful width, then small turn."""
    state=corridor.get('state');width=corridor.get('clear_width_m')
    angle=abs(float(corridor.get('angle_deg',0)))
    if state=='BLOCKED_FAR':
        return (corridor.get('nearest_obstacle_m') or 0)*10-angle*.5
    width=float(width) if isinstance(width,(int,float)) else COMFORTABLE_WIDTH_M
    ground=60 if state=='OBSERVED_CLEAR_CANDIDATE' else 10
    centre=8 if angle<=10 else 0
    return ground+min(max(width,0),1.2)*20+centre-angle*.8


def route_passable(corridor,min_width=MIN_PASSAGE_WIDTH_M):
    width=corridor.get('clear_width_m')
    return (corridor.get('state') in CLEAR_STATES and not corridor.get('gap_m')
        and (not isinstance(width,(int,float)) or width>=min_width))


def closing_risk(trend):
    """Conservative closing test from a short, time-bounded distance window."""
    change=trend.change()
    if not change:return None
    speed=change['speed']
    ttc=max(0.,change['recent_m']-DYNAMIC_CLEARANCE_M)/max(speed,1e-6)
    causes=[]
    if change['drop']>=trend.threshold:causes.append('RAPID_DROP')
    if change['drop']>=.15 and speed>=DYNAMIC_MIN_SPEED_M_S and ttc<=DYNAMIC_TTC_S:
        causes.append('SHORT_TTC')
    if not causes:return None
    return {'drop_m':round(change['drop'],2),'closing_speed_m_s':round(speed,2),
            'ttc_to_clearance_s':round(ttc,2),'clearance_m':DYNAMIC_CLEARANCE_M,
            'span_s':round(change['span_s'],2),'causes':causes}


def camera_axes(points,plane):
    if plane:
        up=np.asarray(plane['normal'],float)
        forward=np.array([0.,0.,1.]);forward-=np.dot(forward,up)*up
        forward/=max(1e-6,np.linalg.norm(forward))
        right=np.cross(forward,up)
        return points@right,points@forward,points@up+plane['offset_m']
    return points[:,0],points[:,2],np.zeros(len(points))


def sector(x,z,h,valid,plane,ground_confirmed,angle):
    """One 0.9m swept band at a given camera-relative angle."""
    a=math.radians(angle);lateral=x*math.cos(a)-z*math.sin(a);along=x*math.sin(a)+z*math.cos(a)
    swept=valid&(np.abs(lateral)<=.45)&(along>.15)&(along<=2.2)
    core=swept&(np.abs(lateral)<=.15)
    # A fitted plane separates floor from obstacles even before the bench
    # reference confirms it; the strong state still requires confirmation.
    obstacles_all=swept&(h>.06)&(h<2.1) if plane else swept
    obstacles=core&(h>.06)&(h<2.1) if plane else core
    obstacle_count=int(obstacles.sum())
    blocked=obstacle_count>=2
    distance=float(np.min(along[obstacles])) if obstacle_count else None
    height_range=None;obstacle_class=None
    if obstacle_count:
        height_range=[round(float(np.percentile(h[obstacles],10)),2),
                      round(float(np.percentile(h[obstacles],90)),2)]
        if height_range[1]<.4:obstacle_class='LOW'
        elif height_range[0]>1.4:obstacle_class='OVERHEAD'
        else:obstacle_class='BODY'
    # Vectorised 4x3 ground-cell binning: coverage counts returns regardless of
    # plane; ground_cells only counts cells sitting on a confirmed plane.
    lo_index=np.floor((along-.6)/.4).astype(np.int32)
    side_index=np.floor((lateral+.45)/.3).astype(np.int32)
    in_cell=((lo_index>=0)&(lo_index<4)&(side_index>=0)&(side_index<3)
        &valid&(np.abs(h)<=.04))
    counts=np.bincount(lo_index[in_cell]*3+side_index[in_cell],minlength=12)
    cell_ok=counts>=3
    coverage=int(cell_ok.sum());supported=coverage if ground_confirmed else 0
    bands=[int(cell_ok[i*3:(i+1)*3].sum()) for i in range(4)]
    gap=None;vertical_transition=None;transition_height=None
    if ground_confirmed:
        for index in range(1,len(bands)):
            if bands[index]==0 and bands[index-1]>=2:
                gap=(.6,1.0,1.4,1.8)[index]
                in_band=obstacles_all&(lo_index==index)
                raised=h[in_band&(h>.06)&(h<.60)]
                if len(raised)>=3:
                    transition_height=round(float(np.percentile(raised,90)),2)
                    vertical_transition=('STEP_UP_OR_CURB' if transition_height<=.45
                        else 'OBSTACLE_EDGE')
                else:vertical_transition='DOWN_STEP_OR_DROP'
                break
    clear_width=0.0
    if not blocked:
        bin_index=np.floor((lateral+.45)/.1).astype(np.int32)
        in_width=(along>=.45)&(along<=1.6)&(bin_index>=0)&(bin_index<=8)
        hits=np.bincount(bin_index[in_width&obstacles_all],minlength=9)
        best_run=0;run=0
        for value in hits:
            if value:run=0
            else:
                run+=1;best_run=max(best_run,run)
        clear_width=round(best_run*.1,2)
    if blocked:
        state='BLOCKED_NEAR' if distance is not None and distance<1.2 else 'BLOCKED_FAR'
    elif ground_confirmed and supported==12:
        state='OBSERVED_CLEAR_CANDIDATE'
    elif coverage>=8:
        state='CLEAR_NO_GROUND_EVIDENCE'
    else:
        state='UNKNOWN'
    return {'state':state,'nearest_obstacle_m':round(distance,2) if distance is not None else None,
        'ground_cells':supported,'coverage_cells':coverage,'required_ground_cells':12,'gap_m':gap,
        'vertical_transition':vertical_transition,'transition_height_m':transition_height,
        'clear_width_m':clear_width,'obstacle_height_range_m':height_range,
        'obstacle_class':obstacle_class}


def corridor_candidates(points,valid,plane,ground_confirmed,axes=None):
    """Observe 0.6-2.2m swept sectors. Unknown ground cells cannot be clear."""
    x,z,h=axes if axes is not None else camera_axes(points,plane);candidates=[]
    for name,angle in (('LEFT',-20),('CENTER',0),('RIGHT',20)):
        candidates.append({'direction':name,'angle_deg':angle,
            **sector(x,z,h,valid,plane,ground_confirmed,angle),
            'width_m':.9,'observed_range_m':[.6,2.2],'body_movement_allowed':False})
    return candidates


def fine_scan(points,valid,plane,ground_confirmed,axes=None):
    """Finer angular sweep used to phrase walking direction; same safety rules."""
    x,z,h=axes if axes is not None else camera_axes(points,plane)
    return [{'angle_deg':angle,**sector(x,z,h,valid,plane,ground_confirmed,angle)} for angle in FINE_ANGLES]


def coarse_direction(angle):
    if angle<-8:return 'LEFT'
    if angle>8:return 'RIGHT'
    return 'CENTER'


def angle_label(angle):
    if angle<=-30:return '左前方'
    if angle<=-20:return '左侧'
    if angle<=-10:return '稍左'
    if angle<10:return '前方'
    if angle<20:return '稍右'
    if angle<30:return '右侧'
    return '右前方'


def near_region_label(indices):
    regions=set(indices)
    if len(regions)==3:return '近处'
    if regions=={0,2}:return '两侧'
    if regions=={0,1}:return '左前方'
    if regions=={1,2}:return '右前方'
    return {0:'左侧',1:'前方',2:'右侧'}.get(next(iter(regions),None),'附近')


def standing_near_cue(levels,regions,urgent=True):
    """Short action cue for standing diagnostics; never grants movement."""
    blocked=set(regions)
    if 1 in blocked:
        safe=[index for index in (0,2) if levels[index] in (1,2)]
        if safe:
            side=min(safe,key=lambda index:(levels[index],index!=0))
            direction='LEFT' if side==0 else 'RIGHT'
            return ('前方近物，往左。' if side==0 else '前方近物，往右。'),direction
        return ('三面近物，停。' if urgent else '前方近物，慢行。'),None
    if blocked=={0,2}:return '两侧近物，走中。','CENTER'
    if blocked=={0}:return '左侧近物，走中。','CENTER'
    if blocked=={2}:return '右侧近物，走中。','CENTER'
    return ('近处有物。' if urgent else '近处近物，慢行。'),None


def describe(corridors):
    """Fuller directional summary for standing mode (not spoken automatically)."""
    if not corridors:return ''
    parts=[]
    for c in corridors:
        label=LABELS[c['direction']];state=c['state'];distance=c.get('nearest_obstacle_m')
        if state in ('BLOCKED_NEAR','BLOCKED_FAR'):
            obstacle=('有低矮障碍' if c.get('obstacle_class')=='LOW'
                else ('上方有障碍' if c.get('obstacle_class')=='OVERHEAD' else '有障碍'))
            parts.append(f"{label}{distance:.1f}米{obstacle}" if isinstance(distance,(int,float)) else f"{label}{obstacle}")
        elif state=='OBSERVED_CLEAR_CANDIDATE':
            width=c.get('clear_width_m')
            parts.append(f"{label}未见障碍"+ (f"，净宽{width:.1f}米" if isinstance(width,(int,float)) else ""))
        elif state=='CLEAR_NO_GROUND_EVIDENCE':parts.append(f"{label}未见障碍，地面未证实")
        else:parts.append(f"{label}未知")
        if c.get('gap_m'):
            transition=c.get('vertical_transition')
            if transition=='STEP_UP_OR_CURB':
                height=c.get('transition_height_m')
                detail=f"约{height:.1f}米" if isinstance(height,(int,float)) else ""
                parts.append(f"{label}{c['gap_m']:.1f}米处疑似{detail}台阶或路缘")
            elif transition=='DOWN_STEP_OR_DROP':
                parts.append(f"{label}{c['gap_m']:.1f}米处疑似下行台阶或边缘")
            else:parts.append(f"{label}{c['gap_m']:.1f}米处地面未确认")
    return '；'.join(parts)+'。'


def describe_objects(info,limit=3,session=None,body_transform=None,body_frame=False):
    """Detected objects with distance, nearest first; candidates only."""
    detection=info.get('detection') or {}
    if detection.get('status') not in (None,'READY'):return ''
    age=detection.get('age_ms')
    if (age is not None and (type(age) not in (int,float) or not math.isfinite(age)
            or age<0 or age>SUMMARY_DETECTION_MAX_AGE_MS)):return ''
    frame=detection.get('frame_id')
    if session is not None and frame is not None and not str(frame).startswith(str(session)+':'):return ''
    objects=detection.get('objects') or []
    rows=[]
    for obj in objects:
        label=LABEL_ZH.get(obj.get('label',''))
        if not label:continue
        distance=obj.get('cluster_distance_m')
        if distance is None:distance=obj.get('depth_patch_m')
        if distance is not None and (type(distance) not in (int,float) or not math.isfinite(distance)):continue
        direction=''
        if body_frame and body_transform and isinstance(distance,(int,float)) and distance>0:
            box=obj.get('bbox') or [0,0,0,0];centre=(box[0]+box[2])/2
            yaw=math.radians(body_transform['yaw_deg'])
            x_cam=(centre-body_transform['cx'])/body_transform['fx']*distance
            body_x=x_cam*math.cos(yaw)+distance*math.sin(yaw)+body_transform['camera_right_m']
            body_z=-x_cam*math.sin(yaw)+distance*math.cos(yaw)+body_transform['camera_forward_m']
            body_angle=math.degrees(math.atan2(body_x,body_z)) if body_z>.05 else None
            if body_angle is not None and abs(body_angle)<=45:
                direction=LABELS[coarse_direction(body_angle)]
        elif not body_frame:
            box=obj.get('bbox') or [0,0,0,0];centre=(box[0]+box[2])/2
            direction=('左侧' if centre<640/3 else ('右侧' if centre>1280/3 else '前方'))
        rows.append((distance if isinstance(distance,(int,float)) else 99.0,label,distance,direction))
    rows.sort(key=lambda row:row[0])
    parts=[f"{direction}{label}{'' if not isinstance(d,(int,float)) else f' {d:.1f}米'}"
        for _,label,d,direction in rows[:limit]]
    return '检测到'+'、'.join(parts)+'。' if parts else ''


class SceneAnalysis:
    def __init__(self):self.motion=CameraMotion();self.model_hash=None;self.pixels=None;self.rays=None

    def evaluate(self,rgb,raw,scale,model,geometry,stamp,session,body_calibration=None,native=None):
        if self.model_hash!=model['sha256']:
            self.pixels,self.rays=make_rays(model,8);self.model_hash=model['sha256']
        z=raw[self.pixels[:,1],self.pixels[:,0]].astype(np.float32)*scale/1000
        valid=np.isfinite(z)&(z>=.3)&(z<=4)
        points=self.rays*z[:,None]
        motion=self.motion.update(rgb,stamp,session)
        confirmed=geometry.get('ground_state')=='REFERENCE_MATCHED_CANDIDATE'
        body_ground=bool(confirmed and body_calibration)
        if motion['state']=='CAMERA_MOVING' and not body_ground:
            return {'corridors':[],'fine':[],'motion_hint':motion,'scope':'CAMERA_BENCH',
                'ground_confirmed':False,'motion_invalidated':True,
                'direction_frame':'CAMERA','calibration_applied':False,
                'native_region_mapping':None,'native_body_levels':None,'native_body_ambiguous':True,
                'body_transform':None,'required_passage_width_m':None,'motion_policy':'BENCH_STATIC_ONLY'}
        plane=geometry.get('plane');axes=None;direction_frame='CAMERA'
        native_mapping=None;body_transform=None
        if confirmed and plane and body_calibration:
            axes=to_body_axes(points,plane,body_calibration);direction_frame='BODY'
            native_mapping=map_native_regions(model,body_calibration,native or [])
            body_transform={'yaw_deg':body_calibration['yaw_deg'],
                'camera_forward_m':body_calibration['camera_forward_m'],
                'camera_right_m':body_calibration['camera_right_m'],
                'required_passage_width_m':body_calibration['required_passage_width_m'],
                'fx':model['fx'],'cx':model['cx'],'width':model['width']}
        return {'corridors':corridor_candidates(points,valid,plane,confirmed,axes),
                'fine':fine_scan(points,valid,plane,confirmed,axes),
                'motion_hint':motion,'scope':'CAMERA_BENCH','ground_confirmed':confirmed,
                'motion_invalidated':False,'direction_frame':direction_frame,
                'calibration_applied':direction_frame=='BODY',
                'native_region_mapping':native_mapping,
                'native_body_levels':native_mapping['body_levels'] if native_mapping else None,
                'native_body_ambiguous':native_mapping['ambiguous'] if native_mapping else True,
                'body_transform':body_transform,
                'required_passage_width_m':body_calibration['required_passage_width_m'] if body_ground else None,
                'motion_policy':'BODY_MOUNT_GROUND_TRACKING' if motion['state']=='CAMERA_MOVING' and body_ground
                    else ('BENCH_STATIC_ONLY' if motion['state']=='CAMERA_MOVING' else 'STATIONARY_OR_UNKNOWN')}


class Guidance:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.RLock();self.mode='STANDING'
        self.requested_mode='AUTO';self.auto_mode=True;self.mode_source='AUTO_WAITING_FOR_IMU'
        self.gait={'state':'UNKNOWN','source':'CAMERA_IMU_NOT_LOCALIZATION'}
        self.session=None;self.last_frame=None;self.choice=None;self.count=0
        self.last_change=0.;self.accepted=None;self.last_key=None;self.last_emit=0.;self.event=0
        self.distance_trend=Trend(.35);self.width_trend=Trend(.2)
        self.history_direction=None;self.trend_frame=None
        self.proposal_seen=0.
        self.hazard_memory={}
        self.pending_key=None;self.pending_since=0.
        self.stable_action=None;self.stable_speech='';self.stable_priority=0;self.stable_direction_hint=None
        self.walk_filtered_levels=None;self.walk_filter_frame=None
        self.walk_basis=None;self.walk_filter_seen=0.
        self.walk_lower_levels=[None,None,None];self.walk_lower_counts=[0,0,0]
        self.detour_direction=None;self.detour_count=0;self.detour_frame=None
        self.result={};self.events=deque(maxlen=30)

    def set_mode(self,mode):
        if mode not in ('AUTO','STANDING','WALKING','PAUSED'):raise ValueError('请选择自动、站立、步行或暂停')
        with self.lock:
            self.requested_mode=mode;self.auto_mode=mode=='AUTO'
            if mode=='AUTO':
                self.mode='WALKING' if self.gait.get('state')=='WALKING' else 'STANDING'
                self.mode_source='IMU_GAIT' if self.gait.get('state') in ('WALKING','STANDING') else 'AUTO_WAITING_FOR_IMU'
            else:
                self.mode=mode;self.mode_source='USER_SELECTED'
            self._reset_state()
        return {'mode':self.mode,'requested_mode':mode,'source':self.mode_source,'movement_allowed':False}

    def _reset_state(self):
        self.last_frame=None;self.choice=None;self.count=0
        self.last_key=None;self.result={};self.accepted=None;self.last_change=0.
        self.distance_trend.clear();self.width_trend.clear();self.history_direction=None
        self.trend_frame=None;self.proposal_seen=0.
        self.hazard_memory.clear()
        self.pending_key=None;self.pending_since=0.
        self.stable_action=None;self.stable_speech='';self.stable_priority=0;self.stable_direction_hint=None
        self.walk_filtered_levels=None;self.walk_filter_frame=None
        self.walk_basis=None;self.walk_filter_seen=0.
        self.walk_lower_levels=[None,None,None];self.walk_lower_counts=[0,0,0]
        self._reset_detour()

    def _filter_walking_levels(self,frame,levels,basis,now):
        """Apply worsening depth immediately; require three distinct frames to clear risk."""
        if len(levels)!=3 or any(type(level) is not int or not 0<=level<=3 for level in levels):
            return levels
        if self.walk_basis!=basis or now-self.walk_filter_seen>.8:
            self.walk_filtered_levels=None
            self.walk_lower_levels=[None,None,None];self.walk_lower_counts=[0,0,0]
        self.walk_basis=basis
        if self.walk_filtered_levels is None:
            self.walk_filtered_levels=list(levels);self.walk_filter_frame=frame
            self.walk_filter_seen=now
            return list(self.walk_filtered_levels)
        if frame is None or frame==self.walk_filter_frame:
            return list(self.walk_filtered_levels)
        self.walk_filter_frame=frame
        self.walk_filter_seen=now
        # Zero means unknown and is more conservative than any measured range.
        severity={1:0,2:1,3:2,0:3}
        for index,observed in enumerate(levels):
            current=self.walk_filtered_levels[index]
            if severity[observed]>=severity[current]:
                self.walk_filtered_levels[index]=observed
                self.walk_lower_levels[index]=None;self.walk_lower_counts[index]=0
            elif self.walk_lower_levels[index]!=observed:
                self.walk_lower_levels[index]=observed;self.walk_lower_counts[index]=1
            else:
                self.walk_lower_counts[index]+=1
                if self.walk_lower_counts[index]>=3:
                    self.walk_filtered_levels[index]=observed
                    self.walk_lower_levels[index]=None;self.walk_lower_counts[index]=0
        return list(self.walk_filtered_levels)

    def _apply_gait(self,imu):
        if not isinstance(imu,dict):return
        self.gait=imu;state=imu.get('state')
        if not self.auto_mode:return
        if state=='WALKING':
            if self.mode!='WALKING':self.mode='WALKING';self.mode_source='IMU_GAIT';self._reset_state()
            else:self.mode_source='IMU_GAIT'
        elif state=='STANDING':
            if self.mode!='STANDING':self.mode='STANDING';self.mode_source='IMU_GAIT';self._reset_state()
            else:self.mode_source='IMU_GAIT'
        elif state in ('UNKNOWN','UNAVAILABLE'):
            self.mode_source='IMU_'+str(state)+'_HOLD'

    def _reset_route(self):
        self.last_frame=None;self.choice=None;self.count=0;self.accepted=None
        self.distance_trend.clear();self.width_trend.clear();self.history_direction=None
        self.trend_frame=None;self.proposal_seen=0.

    def _reset_detour(self):
        self.detour_direction=None;self.detour_count=0;self.detour_frame=None

    def _confirmed_clear_route(self,corridors,fine,excluded,frame,minimum_width):
        """Return a ground-confirmed escape route away from occupied sectors."""
        options=[]
        for corridor in corridors:
            direction=corridor.get('direction');width=corridor.get('clear_width_m')
            if (direction in excluded or corridor.get('state')!='OBSERVED_CLEAR_CANDIDATE'
                    or corridor.get('gap_m') or not isinstance(width,(int,float))
                    or not math.isfinite(width) or width<minimum_width):
                continue
            options.append(corridor)
        if not options:
            self._reset_detour();return None
        options.sort(key=route_rank,reverse=True);route=options[0];direction=route['direction']
        if frame!=self.detour_frame:
            self.detour_count=self.detour_count+1 if direction==self.detour_direction else 1
            self.detour_direction=direction;self.detour_frame=frame
        angle=None
        matching=[c for c in fine if isinstance(c.get('angle_deg'),(int,float))
            and coarse_direction(c['angle_deg'])==direction and c.get('state') in CLEAR_STATES
            and not c.get('gap_m')]
        if matching:
            matching.sort(key=route_rank,reverse=True);angle=matching[0]['angle_deg']
        if self.detour_count<ROUTE_CONFIRM_FRAMES:return None
        return {'direction':direction,'clear_width_m':float(route['clear_width_m']),
                'direction_angle':angle,'confirmation_frames':self.detour_count}

    def update(self,info):
        with self.lock:
            self._apply_gait(info.get('imu'))
            now=self.clock();age=info.get('frame_age_ms')
            turn_rate=(self.gait.get('metrics') or {}).get('turn_rate_dps')
            turning=bool(self.mode=='WALKING' and isinstance(turn_rate,(int,float))
                and math.isfinite(turn_rate) and abs(turn_rate)>=35)
            if turning:self._reset_route();self._reset_detour()
            fresh_limit_ms=500 if self.mode=='STANDING' else 200
            fresh=(info.get('state')=='STREAMING' and type(age) in (int,float) and math.isfinite(age)
                and 0<=age<=fresh_limit_ms)
            session=info.get('session_id');frame=info.get('frame_id')
            if session!=self.session:
                self.session=session;self._reset_state()
            analysis=info.get('guidance_analysis') or {};corridors=analysis.get('corridors') or []
            direction_frame=analysis.get('direction_frame','CAMERA')
            direction_reason='BODY_RELATIVE_DIRECTION' if direction_frame=='BODY' else 'CAMERA_RELATIVE_DIRECTION'
            required_width=analysis.get('required_passage_width_m') if direction_frame=='BODY' else None
            minimum_width=(max(MIN_PASSAGE_WIDTH_M,float(required_width))
                if isinstance(required_width,(int,float)) and math.isfinite(required_width) else MIN_PASSAGE_WIDTH_M)
            comfortable_width=max(COMFORTABLE_WIDTH_M,round(minimum_width+.15,2))
            action='OBSERVE';speech='';priority=0;candidate=None
            direction_hint=None
            reasons=['TURN_IN_PROGRESS'] if turning else [];entry=None
            traffic_decision='STOP'
            choice=None;far_entry=None;route_detail=None;motion_risk=None;cross_modal=None
            detour=None;ground_transition=None;ground_transition_height=None;feedback_regions=[]
            raw_levels=[r.get('level',0) for r in info.get('native_depth',[])]
            body_levels=analysis.get('native_body_levels')
            levels=(body_levels if direction_frame=='BODY' and isinstance(body_levels,list)
                and len(body_levels)==3 else raw_levels)
            if self.mode=='WALKING' and fresh:
                basis=(direction_frame,repr(analysis.get('body_transform')),analysis.get('native_body_ambiguous'))
                levels=self._filter_walking_levels(frame,levels,basis,now)
            cross_modal_map=cross_modal_near_hazards(info,session,
                body_transform=analysis.get('body_transform') if direction_frame=='BODY' else None,
                body_frame=direction_frame=='BODY')
            cross_modal=cross_modal_map.get('CENTER')
            feedback_regions=sorted({i for i,level in enumerate(levels) if type(level) is int and level>=2}
                | {('LEFT','CENTER','RIGHT').index(d) for d in cross_modal_map})
            if self.mode=='PAUSED':action='PAUSED'
            elif not fresh:
                self._reset_route()
                self._reset_detour()
                action='STOP' if self.mode=='WALKING' else 'UNAVAILABLE';priority=90
                speech='停，感知不可用。' if self.mode=='WALKING' else '感知不可用。'
                reasons=['STALE_OR_UNAVAILABLE']
            elif len(levels)!=3 or any(type(x) is not int or not 0<=x<=3 for x in levels):
                self._reset_route()
                self._reset_detour()
                action='STOP' if self.mode=='WALKING' else 'UNAVAILABLE';priority=90
                speech='停，深度异常。' if self.mode=='WALKING' else '深度异常。'
                reasons=['INVALID_DEPTH']
            elif 3 in levels:
                self._reset_route()
                near=[i for i,level in enumerate(levels) if level==3]
                if self.mode=='STANDING':
                    for index in near:self.hazard_memory[index]=now
                    self.hazard_memory={index:stamp for index,stamp in self.hazard_memory.items()
                        if now-stamp<=1.2}
                    recent=set(self.hazard_memory)
                else:recent=set(near)
                feedback_regions=sorted(recent)
                region=near_region_label(recent if self.mode=='STANDING' else near)
                single_region=len(near)==1 and region==('左侧','前方','右侧')[near[0]]
                action='STOP' if self.mode=='WALKING' else 'NEAR_OBJECT';priority=100
                label_direction=None
                if single_region:
                    target=('LEFT','CENTER','RIGHT')[near[0]]
                    mapping=analysis.get('native_region_mapping') or {}
                    if direction_frame=='BODY':
                        label_direction=next((row['camera_region'] for row in mapping.get('regions',[])
                            if row.get('level')==3 and row.get('body_direction')==target),None)
                    else:
                        label_direction=next((('LEFT','CENTER','RIGHT')[i] for i,level in enumerate(raw_levels)
                            if level==3),None)
                hit=nearest_object(info,label_direction,session=session) if label_direction else None
                name=LABEL_ZH.get((hit or {}).get('label',''))
                hazard_frame_ok=(direction_frame=='CAMERA' or (
                    direction_frame=='BODY' and isinstance(body_levels,list)
                    and not analysis.get('native_body_ambiguous')))
                if self.mode=='WALKING' and hazard_frame_ok and len(near)<3:
                    excluded=({('LEFT','CENTER','RIGHT')[i] for i,level in enumerate(levels) if level in (0,3)}
                        | set(cross_modal_map))
                    detour=self._confirmed_clear_route(corridors,analysis.get('fine') or [],excluded,
                        frame,minimum_width)
                    if detour:detour.update({'hazard':region,'frame':direction_frame})
                else:self._reset_detour()
                if detour:
                    action='STOP_AND_DETOUR';priority=99;candidate=detour['direction']
                    direction_hint=candidate
                    traffic_decision='TURN'
                    route=(angle_label(detour['direction_angle']) if detour.get('direction_angle') is not None
                        else LABELS[detour['direction']])
                    speech=(f"停，改走{route}。" if detour['clear_width_m']>=comfortable_width
                        else f"停，改走{route}窄。")
                    reasons+=[('SIDE_HAZARD_DETOUR' if len(near)==1 and near[0] in (0,2)
                        else 'NEAR_HAZARD_DETOUR'),direction_reason]
                elif self.mode=='STANDING':
                    speech,direction_hint=standing_near_cue(levels,recent if recent else near,urgent=True)
                elif name:
                    speech='停，'+region+'可能'+name+'。'
                    reasons.append('OBJECT_LABEL_CANDIDATE')
                else:
                    speech='停，'+region+'有障碍。'
                if cross_modal:reasons.append('CROSS_MODAL_HAZARD')
            elif cross_modal:
                feedback_regions=[('LEFT','CENTER','RIGHT').index(d) for d in cross_modal_map]
                self._reset_route()
                name=LABEL_ZH.get(cross_modal['label'])
                if self.mode=='WALKING':
                    excluded=({'CENTER'}|{('LEFT','CENTER','RIGHT')[i] for i,level in enumerate(levels) if level==0}
                        | set(cross_modal_map))
                    detour=self._confirmed_clear_route(corridors,analysis.get('fine') or [],
                        excluded,frame,minimum_width)
                    if detour:
                        detour.update({'hazard':'前方','frame':direction_frame})
                        action='STOP_AND_DETOUR';priority=99;candidate=detour['direction']
                        direction_hint=candidate
                        traffic_decision='TURN'
                        route=(angle_label(detour['direction_angle']) if detour.get('direction_angle') is not None
                            else LABELS[detour['direction']])
                        speech=(f"停，改走{route}。" if detour['clear_width_m']>=comfortable_width
                            else f"停，改走{route}窄。")
                        reasons+=['NEAR_HAZARD_DETOUR',direction_reason]
                    else:
                        action='STOP';priority=98
                        speech=(f"停，前方可能{name}。" if name else '停，前方有物。')
                else:
                    action='NEAR_OBJECT';priority=60
                    speech,direction_hint=standing_near_cue(levels,feedback_regions,urgent=False)
                reasons.append('CROSS_MODAL_HAZARD')
                if name:reasons.append('DETECTION_LABEL_CANDIDATE')
            elif ((self.mode=='WALKING' and levels[1]==0)
                    or (self.mode=='STANDING' and 0 in levels)):
                self._reset_route()
                self._reset_detour()
                action='STOP' if self.mode=='WALKING' else 'UNKNOWN';priority=90 if self.mode=='WALKING' else 40
                speech='停，深度不足。' if self.mode=='WALKING' else '部分未知。'
                reasons=['UNKNOWN_DEPTH']
            else:
                self._reset_detour()
                unknown_directions={('LEFT','CENTER','RIGHT')[i] for i,level in enumerate(levels) if level==0}
                if unknown_directions:reasons.append('UNKNOWN_DEPTH_ELSEWHERE')
                cross_modal_directions=set(cross_modal_map)
                if cross_modal_directions:reasons.append('CROSS_MODAL_SIDE_HAZARD')
                blocked_directions=unknown_directions|cross_modal_directions
                scan=analysis.get('fine') or []
                fine=[c for c in scan if c['state'] in CLEAR_STATES and not c.get('gap_m')
                    and coarse_direction(c['angle_deg']) not in blocked_directions]
                fine.sort(key=route_rank,reverse=True)
                passable=[c for c in fine if route_passable(c,minimum_width)]
                narrow=[c for c in fine if not route_passable(c,minimum_width)]
                far=[c for c in scan if c['state']=='BLOCKED_FAR' and not c.get('gap_m')
                    and coarse_direction(c['angle_deg']) not in blocked_directions]
                far.sort(key=lambda c:((c.get('nearest_obstacle_m') or 0),-abs(c['angle_deg'])),reverse=True)
                strong=[c for c in corridors if c['state']=='OBSERVED_CLEAR_CANDIDATE'
                    and c['direction'] not in blocked_directions]
                weak=[c for c in corridors if c['state']=='CLEAR_NO_GROUND_EVIDENCE'
                    and c['direction'] not in blocked_directions]
                corridor_routes=sorted(strong+weak,key=lambda c:(route_rank(c),c['direction']=='CENTER'),reverse=True)
                if passable:
                    routes=passable
                elif narrow:
                    routes=narrow
                elif far:
                    routes=far
                else:
                    routes=corridor_routes
                valid_choices={coarse_direction(c['angle_deg']) for c in routes} if routes and 'angle_deg' in routes[0] else {
                    c['direction'] for c in routes}
                choice=coarse_direction(routes[0]['angle_deg']) if routes and 'angle_deg' in routes[0] else (
                    routes[0]['direction'] if routes else None)
                center_route=next((c for c in fine if coarse_direction(c['angle_deg'])=='CENTER'),None)
                if center_route is None:
                    center_route=next((c for c in corridors if c['direction']=='CENTER'),None)
                center_width=(center_route or {}).get('clear_width_m')
                center_ready=bool(center_route and route_passable(center_route,minimum_width)
                    and isinstance(center_width,(int,float)) and math.isfinite(center_width)
                    and center_width>=max(minimum_width,CENTER_RECENTER_MIN_WIDTH_M))
                forward_too_narrow=bool(center_route and isinstance(center_width,(int,float))
                    and math.isfinite(center_width) and center_width<minimum_width)
                recenter=(self.accepted in ('LEFT','RIGHT') and choice=='CENTER' and center_ready)
                prior_side=self.accepted in ('LEFT','RIGHT')
                if self.accepted in valid_choices and routes and not recenter:
                    accepted_route=next((c for c in routes if coarse_direction(c['angle_deg'])==self.accepted),None) if 'angle_deg' in routes[0] else next(
                        (c for c in routes if c['direction']==self.accepted),None)
                    if accepted_route is not None and route_rank(routes[0])-route_rank(accepted_route)<=ROUTE_SWITCH_MARGIN:
                        choice=self.accepted
                if frame!=self.last_frame:
                    restart=choice!=self.choice or now-self.proposal_seen>.8
                    self.count=1 if restart else self.count+1
                    self.choice=choice;self.last_frame=frame;self.proposal_seen=now
                if self.accepted not in valid_choices:self.accepted=None
                if choice and self.count>=ROUTE_CONFIRM_FRAMES:
                    if choice!=self.accepted:self.last_change=now
                    self.accepted=choice
                candidate=self.accepted
                entry=next((c for c in fine if coarse_direction(c['angle_deg'])==candidate),None)
                far_entry=next((c for c in far if coarse_direction(c['angle_deg'])==candidate),None)
                if entry:direction=angle_label(entry['angle_deg'])
                elif far_entry:direction=angle_label(far_entry['angle_deg'])
                else:direction=LABELS.get(candidate,'')
                strong_directions={c['direction'] for c in strong}
                weak_directions={c['direction'] for c in weak}
                strong_candidate=(entry['state']=='OBSERVED_CLEAR_CANDIDATE') if entry else (candidate in strong_directions)
                if entry is not None:
                    route_detail={'kind':entry['state'],'angle_deg':entry['angle_deg'],
                        'clear_width_m':entry.get('clear_width_m'),'score':round(route_rank(entry),1),
                        'frame':direction_frame}
                elif far_entry is not None:
                    route_detail={'kind':'BLOCKED_FAR','angle_deg':far_entry['angle_deg'],
                        'nearest_obstacle_m':far_entry.get('nearest_obstacle_m'),'score':None,
                        'frame':direction_frame,'obstacle_class':far_entry.get('obstacle_class'),
                        'obstacle_height_range_m':far_entry.get('obstacle_height_range_m')}
                elif candidate is not None:
                    route_detail={'kind':'CORRIDOR_FALLBACK','direction':candidate,'score':None,
                        'frame':direction_frame}
                else:route_detail=None
                if self.mode=='WALKING':
                    # A static bench reference never becomes body calibration.
                    action='STOP_AND_CONFIRM';priority=80
                    pose_reasons=([] if direction_frame=='BODY' else ['BODY_POSE_UNVERIFIED'])
                    reasons=pose_reasons+['NEAR_GROUND_BLIND_ZONE']+reasons
                    if prior_side and candidate=='CENTER':reasons.append('CENTER_REACQUIRED')
                    clear_candidate=candidate is not None and (
                        entry is not None or candidate in strong_directions or candidate in weak_directions)
                    passable_candidate=clear_candidate and (entry is None or route_passable(entry,minimum_width))
                    gap_entry=next((c for c in scan
                        if c.get('gap_m') and c['gap_m']<=1.8 and abs(c['angle_deg'])<=10),None)
                    if gap_entry:
                        ground_transition=gap_entry.get('vertical_transition')
                        ground_transition_height=gap_entry.get('transition_height_m')
                    closing=False;distance_value=None
                    if entry is None and far_entry is not None:distance_value=far_entry.get('nearest_obstacle_m')
                    width_value=(entry or {}).get('clear_width_m');width_for_speech=None
                    if candidate:
                        if candidate!=self.history_direction:
                            self.distance_trend.clear();self.width_trend.clear()
                            self.history_direction=candidate
                            self.trend_frame=None
                        if frame!=self.trend_frame:
                            if isinstance(distance_value,(int,float)):self.distance_trend.add(now,distance_value)
                            else:self.distance_trend.clear()
                            if isinstance(width_value,(int,float)):self.width_trend.add(now,width_value)
                            else:self.width_trend.clear()
                            self.trend_frame=frame
                        motion_risk=closing_risk(self.distance_trend)
                        closing=motion_risk is not None
                        narrowing=self.width_trend.dropped()
                        if self.width_trend.samples:width_for_speech=statistics.median(
                            value for _,value in self.width_trend.samples)
                    else:
                        self.distance_trend.clear();self.width_trend.clear();self.trend_frame=None
                    if closing:
                        action='STOP';priority=95;speech='停，'+direction+'靠近。'
                        reasons.append('CLOSING_FAST')
                        if 'SHORT_TTC' in motion_risk['causes']:reasons.append('CLOSING_SHORT_TTC')
                    elif gap_entry and passable_candidate:
                        action='DIRECTION_CANDIDATE';priority=89;traffic_decision='TURN'
                        if ground_transition=='STEP_UP_OR_CURB':
                            speech=f"停，台阶，改走{direction}。"
                            reasons+=['GROUND_GAP','STEP_UP_OR_CURB',direction_reason]
                        else:
                            speech=f"停，落差，改走{direction}。"
                            reasons+=['GROUND_GAP','DOWN_STEP_OR_DROP',direction_reason]
                    elif gap_entry:
                        action='STOP';priority=90
                        if ground_transition=='STEP_UP_OR_CURB':
                            speech='停，有台阶。'
                            reasons+=['GROUND_GAP','STEP_UP_OR_CURB']
                        else:
                            speech='停，有落差。';reasons+=['GROUND_GAP','DOWN_STEP_OR_DROP']
                    elif candidate and isinstance(width_value,(int,float)) and width_value<minimum_width:
                        action='STOP';priority=88
                        speech='停，通道过窄。';reasons.append('NARROW_CORRIDOR')
                    elif passable_candidate and strong_candidate:
                        action='DIRECTION_CANDIDATE';priority=75
                        width=width_value
                        stable_width=width_for_speech if width_for_speech is not None else width
                        if forward_too_narrow and candidate!='CENTER':
                            traffic_decision='TURN'
                            speech=f"停，改走{direction}。"
                            reasons.append('FORWARD_TOO_NARROW')
                        elif narrowing:
                            traffic_decision='GO'
                            priority=84;speech='能走，'+direction+'变窄。'
                            reasons.append('CORRIDOR_NARROWING')
                        elif stable_width is not None and stable_width<comfortable_width:
                            traffic_decision='GO'
                            speech=direction+'可走，窄。';reasons.append('NARROW_CORRIDOR')
                        else:
                            traffic_decision='GO'
                            speech=direction+'可走。'
                        reasons.append(direction_reason)
                    elif passable_candidate:
                        action='DIRECTION_CANDIDATE';priority=70
                        traffic_decision='GO'
                        speech=direction+'可走。'
                        reasons+=[direction_reason,'GROUND_UNCONFIRMED']
                    elif candidate and far_entry:
                        action='STOP';priority=65
                        if far_entry.get('obstacle_class')=='LOW':
                            speech=f"停，{direction}低物。"
                        elif far_entry.get('obstacle_class')=='OVERHEAD':
                            speech=f"停，{direction}上方有物。"
                        else:speech=f"停，{direction}有物。"
                        reasons+=[direction_reason,'OBSTACLE_AHEAD']
                    else:
                        near=any(c['state']=='BLOCKED_NEAR' for c in scan) or any(c['state']=='BLOCKED_NEAR' for c in corridors)
                        if choice is not None and candidate is None:
                            action='OBSERVE';priority=20;speech='正在确认路线。';reasons.append('ROUTE_CONFIRMING')
                        elif cross_modal_directions:
                            side_hazard=min((cross_modal_map[d] for d in cross_modal_directions),
                                key=lambda item:item['distance_m'])
                            label=LABEL_ZH.get(side_hazard['label'])
                            side=LABELS[side_hazard['direction']]
                            action='STOP';priority=95
                            speech=(f"停，{side}可能{label}。" if label else f"停，{side}有物。")
                        else:speech='停，前方有障碍。' if near else '停，方向未知。'
                elif 2 in levels:
                    for i,level in enumerate(levels):
                        if level==2:self.hazard_memory[i]=now
                    self.hazard_memory={i:t for i,t in self.hazard_memory.items() if now-t<=1.2}
                    feedback_regions=sorted(self.hazard_memory)
                    region=near_region_label(feedback_regions)
                    action='NEAR_OBJECT';priority=50
                    if self.mode=='STANDING':
                        speech,direction_hint=standing_near_cue(levels,feedback_regions,urgent=False)
                    else:
                        speech='停，'+region+'近物。'
            observed_key=(self.mode,action,speech)
            if (self.mode=='STANDING' and fresh and action!='UNAVAILABLE' and priority<=self.stable_priority
                    and self.last_key is not None and observed_key!=self.last_key):
                if observed_key!=self.pending_key:
                    self.pending_key=observed_key;self.pending_since=now
                if now-self.pending_since<.3 and self.stable_action is not None:
                    action=self.stable_action;speech=self.stable_speech;priority=self.stable_priority
                    direction_hint=self.stable_direction_hint
                    feedback_regions=list(self.result.get('hazard_regions',[]))
                else:
                    self.pending_key=None
            else:
                self.pending_key=None
            if ground_transition is None:
                transition_entry=next((c for c in corridors if c.get('vertical_transition')),None)
                if transition_entry:
                    ground_transition=transition_entry['vertical_transition']
                    ground_transition_height=transition_entry.get('transition_height_m')
            key=(self.mode,action,speech)
            # One current message, no stale speech backlog. Walking repeats the
            # current cue slowly so a missed reminder does not disappear; urgent
            # cues use the shorter interval. Standing changes only.
            repeat_s=(URGENT_REPEAT_S if priority>=80 else (ROUTE_REPEAT_S if priority>=65 else None))
            repeat=(self.mode=='WALKING' and repeat_s is not None
                and now-self.last_emit>=repeat_s)
            if key!=self.last_key or repeat:
                if speech:
                    self.event+=1;self.events.appendleft({'id':self.event,'text':speech,'action':action,'frame_id':frame})
                self.last_key=key;self.last_emit=now
                self.stable_action=action;self.stable_speech=speech;self.stable_priority=priority
                self.stable_direction_hint=direction_hint
            self.result={'mode':self.mode,'requested_mode':self.requested_mode,
                'mode_source':self.mode_source,'action':action,'speech':speech,
                'priority':priority,'event_id':self.event,'frame_id':frame,'candidate':candidate,
                'direction':candidate,'direction_hint':direction_hint,
                'summary':(describe(corridors)+describe_objects(info,session=session,
                    body_transform=analysis.get('body_transform') if direction_frame=='BODY' else None,
                    body_frame=direction_frame=='BODY')) if fresh else '',
                'direction_angle':entry['angle_deg'] if entry else (far_entry['angle_deg'] if far_entry else None),
                'route':route_detail,'confirmation_frames':self.count if candidate is not None and choice==candidate else 0,
                'motion_risk':motion_risk,
                'cross_modal_hazard':cross_modal,
                'cross_modal_hazards':cross_modal_map or None,
                'detour':detour,
                'ground_transition':ground_transition,
                'ground_transition_height_m':ground_transition_height,
                'required_passage_width_m':minimum_width,
                'comfortable_passage_width_m':comfortable_width,
                'direction_frame':direction_frame,
                'hazard_regions':feedback_regions if fresh else [],
                'filtered_levels':levels if fresh else None,
                'native_body_levels':body_levels if direction_frame=='BODY' else None,
                'native_body_ambiguous':analysis.get('native_body_ambiguous',True),
                'policy':{'output':'FULL_SUMMARY' if self.mode=='STANDING' else 'SINGLE_ACTION',
                    'auto_mode':self.auto_mode,'gait_state':self.gait.get('state','UNKNOWN'),
                    'turn_in_progress':turning,
                    'repeat_urgent_s':URGENT_REPEAT_S if self.mode=='WALKING' else None,
                    'repeat_direction_s':ROUTE_REPEAT_S if self.mode=='WALKING' else None,
                    'center_recenter_min_width_m':CENTER_RECENTER_MIN_WIDTH_M if self.mode=='WALKING' else None,
                    'fresh_limit_ms':fresh_limit_ms},
                'corridors':corridors if fresh else [],'motion_hint':analysis.get('motion_hint',{'state':'UNKNOWN'}),
                'gait':self.gait,
                'traffic_decision':traffic_decision,
                'can_continue':traffic_decision in ('GO','TURN'),
                'must_turn':traffic_decision=='TURN',
                'reasons':reasons,'movement_allowed':False,
                'scope':'BODY_MOUNT_CANDIDATE' if direction_frame=='BODY' else 'CAMERA_BENCH',
                'fresh':fresh,'events':list(self.events)}
            return dict(self.result)


def overlay(image,analysis):
    h,w=image.shape[:2]
    for i,c in enumerate(analysis.get('corridors',[])):
        color={'BLOCKED_NEAR':(50,80,240),'BLOCKED_FAR':(0,150,255),'UNKNOWN':(140,140,140),
            'OBSERVED_CLEAR_CANDIDATE':(0,210,220),'CLEAR_NO_GROUND_EVIDENCE':(0,180,160)}.get(c['state'],(140,140,140))
        x=int((i+.5)*w/3)
        cv2.arrowedLine(image,(w//2,h-12),(x,int(h*.63)),color,3,tipLength=.12)
        cv2.putText(image,c['direction']+' '+c['state'].replace('OBSERVED_CLEAR_CANDIDATE','CANDIDATE'),
            (max(2,x-88),h-28),cv2.FONT_HERSHEY_SIMPLEX,.36,color,1)
    return image
