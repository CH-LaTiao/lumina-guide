"""Deterministic algorithm acceptance suite for standing/walking policy."""
import argparse
import json
from pathlib import Path

import numpy as np

from guidance import Guidance, corridor_candidates, fine_scan


def floor_points(obstacles=(),zmax=2.2):
    x,z=np.meshgrid(np.arange(-2,2,.04),np.arange(.6,zmax,.04))
    points=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()]).astype(float)
    for obstacle in obstacles:
        points=np.vstack([points,np.array(obstacle,dtype=float)])
    return points,np.ones(len(points),bool),{'normal':[0,-1,0],'offset_m':1.}


def analysis(points,valid,plane,confirmed=True):
    return {'corridors':corridor_candidates(points,valid,plane,confirmed),
            'fine':fine_scan(points,valid,plane,confirmed),
            'motion_hint':{'state':'CAMERA_STILL'},'direction_frame':'CAMERA',
            'native_body_ambiguous':True}


def info(index,guide,levels=(1,1,1),detection=None):
    value={'state':'STREAMING','frame_age_ms':50,'session_id':'bench','frame_id':f'bench:{index}',
           'native_depth':[{'level':x} for x in levels],'guidance_analysis':guide}
    if detection:value['detection']=detection
    return value


def walk(guide,levels=(1,1,1),frames=4,detection=None):
    current=[1.]
    policy=Guidance(clock=lambda:current[0]);policy.set_mode('WALKING');result=None
    for index in range(1,frames+1):
        current[0]=1+index*.2;result=policy.update(info(index,guide,levels,detection))
    return result


def case(name,result,checks):
    failed=[label for label,predicate in checks if not predicate]
    return {'name':name,'passed':not failed,'failed_checks':failed,
            'actual':{'action':result.get('action'),'priority':result.get('priority'),
                'speech':result.get('speech'),'candidate':result.get('candidate'),
                'reasons':result.get('reasons'),'direction_frame':result.get('direction_frame')}}


def run():
    cases=[]
    points,valid,plane=floor_points()
    result=walk(analysis(points,valid,plane))
    cases.append(case('open_floor',result,[
        ('direction',lambda:result['action']=='DIRECTION_CANDIDATE'),
        ('forward',lambda:result['candidate']=='CENTER'),
        ('short_speech',lambda:result['speech']=='前方可走。'),
        ('go',lambda:result['traffic_decision']=='GO' and result['can_continue'])]))

    points,valid,plane=floor_points([[[0,.5,1.0],[.04,.5,1.0]]])
    result=walk(analysis(points,valid,plane))
    cases.append(case('centre_obstacle',result,[
        ('direction',lambda:result['action']=='DIRECTION_CANDIDATE'),
        ('side',lambda:'左' in result['speech'])]))

    columns=[]
    for side in (-.3,.3):columns.extend([[side,.5,z] for z in (.6,.9,1.2,1.5)])
    points,valid,plane=floor_points([columns])
    result=walk(analysis(points,valid,plane))
    cases.append(case('narrow_gap',result,[
        ('narrow',lambda:'窄' in result['speech']),
        ('short',lambda:len(result['speech'])<=10),
        ('go',lambda:result['traffic_decision']=='GO')]))

    points,valid,plane=floor_points(zmax=1.4)
    result=walk(analysis(points,valid,plane))
    cases.append(case('ground_gap',result,[
        ('stop',lambda:result['action']=='STOP'),
        ('gap',lambda:'落差' in result['speech']),
        ('no_continue',lambda:not result['can_continue'])]))

    x,z=np.meshgrid(np.arange(-1,1,.04),np.arange(.6,1.0,.04))
    floor=np.column_stack([x.ravel(),np.ones(x.size),z.ravel()])
    x,z=np.meshgrid(np.arange(-1,1,.04),np.arange(1.0,2.2,.04))
    raised=np.column_stack([x.ravel(),np.full(x.size,.88),z.ravel()])
    points=np.vstack([floor,raised]);valid=np.ones(len(points),bool)
    plane={'normal':[0,-1,0],'offset_m':1.}
    result=walk(analysis(points,valid,plane))
    cases.append(case('step_up_or_curb',result,[
        ('stop',lambda:result['action']=='STOP'),
        ('step',lambda:'台阶' in result['speech']),
        ('reason',lambda:'STEP_UP_OR_CURB' in result['reasons'])]))

    corridors=[{'direction':'LEFT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
        {'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
        {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.5}]
    result=walk({'corridors':corridors,'fine':[]},levels=(3,1,1))
    cases.append(case('side_hazard_detour',result,[
        ('detour',lambda:result['action']=='STOP_AND_DETOUR'),
        ('direction',lambda:result['candidate']=='CENTER'),
        ('speech',lambda:'改走前方' in result['speech']),
        ('turn',lambda:result['traffic_decision']=='TURN' and result['must_turn'])]))

    corridors=[{'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8},
        {'direction':'CENTER','state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
        {'direction':'RIGHT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.0}]
    result=walk({'corridors':corridors,'fine':[{'angle_deg':20,'state':'OBSERVED_CLEAR_CANDIDATE',
        'clear_width_m':1.0}]},levels=(1,3,1))
    cases.append(case('centre_hazard_detour',result,[
        ('detour',lambda:result['action']=='STOP_AND_DETOUR'),
        ('direction',lambda:result['candidate']=='RIGHT'),
        ('speech',lambda:'改走右侧' in result['speech'])]))

    result=walk({'corridors':[],'fine':[
        {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2},
        {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}]},levels=(0,1,1))
    cases.append(case('side_depth_unknown_centre_route',result,[
        ('direction',lambda:result['action']=='DIRECTION_CANDIDATE' and result['candidate']=='CENTER'),
        ('unknown_sector_ignored',lambda:'UNKNOWN_DEPTH_ELSEWHERE' in result['reasons']),
        ('short_speech',lambda:'前方可走' in result['speech'])]))

    repeat_guide={'corridors':[{'direction':'CENTER','state':'OBSERVED_CLEAR_CANDIDATE',
        'clear_width_m':.9}],'fine':[{'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE',
        'clear_width_m':.9}]}
    now=[1.];policy=Guidance(clock=lambda:now[0]);policy.set_mode('WALKING');result=None
    for index in range(1,5):
        now[0]=1+index*.2;result=policy.update(info(index,repeat_guide))
    first_event=result['event_id'];now[0]=9.6
    unchanged=policy.update(info(5,repeat_guide))
    now[0]=9.8;result=policy.update(info(6,repeat_guide))
    cases.append(case('walking_route_repeat',result,[
        ('not_premature',lambda:unchanged['event_id']==first_event),
        ('repeat_after_8s',lambda:result['event_id']>first_event),
        ('same_short_speech',lambda:result['speech']=='前方可走。'),
        ('policy',lambda:result['policy']['repeat_direction_s']==8)]))

    stale_detection={'status':'READY','age_ms':1500,'frame_id':'bench:1','objects':[
        {'label':'chair','bbox':[0,0,10,10],'depth_patch_m':1.2}]}
    result=Guidance().update(info(1,{'corridors':[{'direction':'CENTER',
        'state':'CLEAR_NO_GROUND_EVIDENCE'}],'fine':[]},detection=stale_detection))
    cases.append(case('standing_stale_object_hidden',result,[
        ('full_summary',lambda:result['policy']['output']=='FULL_SUMMARY'),
        ('no_stale_label',lambda:'椅子' not in result['summary']),
        ('no_movement',lambda:result['movement_allowed'] is False)]))

    guide={'corridors':[{'direction':'CENTER','state':'CLEAR_NO_GROUND_EVIDENCE'}],
        'fine':[],'direction_frame':'BODY','body_transform':{
            'yaw_deg':0,'camera_forward_m':0,'camera_right_m':0,'fx':400.,'cx':320.,'width':640}}
    detection={'status':'READY','age_ms':60,'frame_id':'bench:1','objects':[
        {'label':'chair','bbox':[100,260,140,430],'cluster_distance_m':1.0}]}
    result=Guidance().update(info(1,guide,detection=detection))
    cases.append(case('standing_body_object_direction',result,[
        ('direction',lambda:'左侧椅子 1.0米' in result['summary']),
        ('body_frame',lambda:result['direction_frame']=='BODY')]))

    policy=Guidance();policy.set_mode('WALKING');result=None
    initial={'corridors':[],'fine':[
        {'angle_deg':0,'state':'BLOCKED_NEAR','nearest_obstacle_m':.5},
        {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]}
    for index in range(1,5):result=policy.update(info(index,initial))
    restored={'corridors':[],'fine':[
        {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8},
        {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.2}]}
    for index in range(5,9):result=policy.update(info(index,restored))
    cases.append(case('centre_route_reacquired',result,[
        ('direction',lambda:result['candidate']=='CENTER'),
        ('reason',lambda:'CENTER_REACQUIRED' in result['reasons']),
        ('short_speech',lambda:'前方可走' in result['speech'])]))

    now=[1.];policy=Guidance(clock=lambda:now[0]);policy.set_mode('WALKING');result=None
    for index,distance in ((1,1.8),(2,1.8),(3,1.8),(4,1.8),(5,1.5),(6,1.45),(7,1.4)):
        now[0]=1+index*.2
        f=info(index,{'corridors':[],'fine':[{'angle_deg':0,'state':'BLOCKED_FAR',
            'nearest_obstacle_m':distance}]})
        result=policy.update(f)
    cases.append(case('short_ttc',result,[
        ('stop',lambda:result['action']=='STOP'),
        ('ttc',lambda:'CLOSING_SHORT_TTC' in result['reasons']),
        ('metric',lambda:result['motion_risk']['ttc_to_clearance_s']<=1.2)]))

    detection={'status':'READY','age_ms':60,'frame_id':'bench:1','objects':[
        {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
    result=Guidance();policy=result;policy.set_mode('WALKING')
    result=policy.update(info(4,{'corridors':[],'fine':[]},detection=detection))
    cases.append(case('cross_modal_near',result,[
        ('cross_modal',lambda:'CROSS_MODAL_HAZARD' in result['reasons']),
        ('stop',lambda:result['action']=='STOP'),
        ('label',lambda:'椅子' in result['speech'])]))

    detection={'status':'READY','age_ms':60,'frame_id':'bench:1','objects':[
        {'label':'chair','bbox':[280,300,360,380],'depth_patch_m':.75}]}
    guide={'corridors':[
        {'direction':'LEFT','state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9},
        {'direction':'RIGHT','state':'BLOCKED_NEAR','nearest_obstacle_m':.6}],
        'fine':[{'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.9}]}
    result=walk(guide,detection=detection)
    cases.append(case('cross_modal_side_detour',result,[
        ('detour',lambda:result['action']=='STOP_AND_DETOUR'),
        ('direction',lambda:result['candidate']=='LEFT'),
        ('speech',lambda:'改走左侧' in result['speech'])]))

    detection={'status':'READY','age_ms':60,'frame_id':'bench:1','objects':[
        {'label':'person','bbox':[60,260,140,430],'depth_patch_m':.75}]}
    guide={'corridors':[],'fine':[
        {'angle_deg':-20,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':1.0},
        {'angle_deg':0,'state':'OBSERVED_CLEAR_CANDIDATE','clear_width_m':.8}]}
    result=walk(guide,detection=detection)
    cases.append(case('cross_modal_side_route_excluded',result,[
        ('centre',lambda:result['candidate']=='CENTER'),
        ('reason',lambda:'CROSS_MODAL_SIDE_HAZARD' in result['reasons']),
        ('mapped',lambda:result['cross_modal_hazards']['LEFT']['direction']=='LEFT')]))

    detection={'status':'READY','age_ms':60,'frame_id':'bench:1','objects':[
        {'label':'person','bbox':[60,260,140,430],'depth_patch_m':.75}]}
    policy=Guidance();policy.set_mode('WALKING')
    result=policy.update(info(1,{'corridors':[],'fine':[]},detection=detection))
    cases.append(case('cross_modal_side_no_route',result,[
        ('stop',lambda:result['action']=='STOP'),
        ('specific',lambda:'左侧可能有行人' in result['speech']),
        ('priority',lambda:result['priority']==95)]))

    guide={'corridors':corridors,'fine':[],'direction_frame':'BODY',
           'native_body_levels':[3,1,1],'native_body_ambiguous':True,
           'native_region_mapping':{'regions':[
               {'camera_region':'LEFT','body_direction':'LEFT','level':3},
               {'camera_region':'CENTER','body_direction':'CENTER','level':1},
               {'camera_region':'RIGHT','body_direction':'RIGHT','level':1}]}}
    result=walk(guide,levels=(3,1,1),frames=5)
    cases.append(case('ambiguous_body_hazard',result,[
        ('no_detour',lambda:result['detour'] is None),
        ('not_detour_action',lambda:result['action']!='STOP_AND_DETOUR'),
        ('body_frame',lambda:result['direction_frame']=='BODY')]))

    stale=Guidance();stale.set_mode('WALKING');f=info(1,{'corridors':[],'fine':[]});f['frame_age_ms']=201
    result=stale.update(f)
    cases.append(case('stale_depth',result,[
        ('stop',lambda:result['action']=='STOP'),
        ('reason',lambda:'STALE_OR_UNAVAILABLE' in result['reasons'])]))

    return {'algorithm':'guidance-benchmark-4','passed':all(x['passed'] for x in cases),
            'cases':cases,'counts':{'total':len(cases),'passed':sum(x['passed'] for x in cases)}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Evidence exists; choose a new name')
    report=run()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'counts':report['counts']},ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)
