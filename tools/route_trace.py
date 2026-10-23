"""Read-only JSONL route trace for controlled field tests. Never stores images."""
import argparse
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path


def make_record(status,label):
    geometry=status.get('geometry') or {}
    guidance=status.get('guidance') or {}
    analysis=status.get('guidance_analysis') or {}
    detection=status.get('detection') or {}
    readiness=status.get('readiness') or {}
    imu=status.get('imu') or {}
    body=geometry.get('body_calibration') or {}
    software=status.get('software') or {}
    corridors=[]
    for corridor in guidance.get('corridors') or []:
        corridors.append({key:corridor.get(key) for key in
            ('direction','state','nearest_obstacle_m','clear_width_m','obstacle_class',
             'obstacle_height_range_m','gap_m','ground_cells','required_ground_cells')})
    return {
        'wall_time':round(time.time(),3),'label':label,
        'software_version':status.get('software_version'),'software_commit':software.get('commit'),
        'software_source':software.get('source'),'code_root':software.get('code_root'),
        'state':status.get('state'),'session_id':status.get('session_id'),'frame_id':status.get('frame_id'),
        'frame_age_ms':status.get('frame_age_ms'),'camera_fps':status.get('camera_fps'),
        'guidance_ms':status.get('guidance_ms'),'provider':detection.get('provider'),
        'ground_state':geometry.get('ground_state'),'body_calibration':body.get('state','UNSET'),
        'readiness_stage':readiness.get('stage'),'movement_ready':readiness.get('ready_for_movement'),
        'direction_frame':guidance.get('direction_frame'),'action':guidance.get('action'),
        'traffic_decision':guidance.get('traffic_decision'),
        'can_continue':guidance.get('can_continue'),'must_turn':guidance.get('must_turn'),
        'requested_mode':guidance.get('requested_mode'),'mode_source':guidance.get('mode_source'),
        'gait_state':imu.get('state'),'gait_metrics':imu.get('metrics'),
        'priority':guidance.get('priority'),'speech':guidance.get('speech'),
        'candidate':guidance.get('candidate'),'event_id':guidance.get('event_id'),
        'route':guidance.get('route'),'motion_risk':guidance.get('motion_risk'),'detour':guidance.get('detour'),
        'cross_modal_hazards':guidance.get('cross_modal_hazards'),
        'ground_transition':guidance.get('ground_transition'),
        'ground_transition_height_m':guidance.get('ground_transition_height_m'),
        'required_passage_width_m':guidance.get('required_passage_width_m'),
        'motion_policy':analysis.get('motion_policy'),'reasons':guidance.get('reasons') or [],
        'corridors':corridors,'movement_allowed':guidance.get('movement_allowed'),
    }


def summarise(records,elapsed):
    def count(key):return dict(sorted(Counter(row.get(key) for row in records).items(),
                                      key=lambda item:str(item[0])))
    return {'duration_s':round(elapsed,1),'records':len(records),'states':count('state'),
            'actions':count('action'),'traffic_decisions':count('traffic_decision'),
            'stages':count('readiness_stage'),'direction_frames':count('direction_frame'),
            'body_calibration':count('body_calibration'),'motion_risks':sum(bool(x.get('motion_risk')) for x in records),
            'gait_states':count('gait_state'),
            'detours':sum(bool(x.get('detour')) for x in records),
            'cross_modal_hazards':sum(bool(x.get('cross_modal_hazards')) for x in records),
            'speeches':len({x.get('speech') for x in records if x.get('speech')}),
            'frames':len({x.get('frame_id') for x in records if x.get('frame_id')}),
            'physical_output':False,'images_saved':0,'cloud_calls':0}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='http://127.0.0.1:8765')
    parser.add_argument('--seconds',type=float,default=60)
    parser.add_argument('--interval',type=float,default=.1)
    parser.add_argument('--label',default='controlled')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.seconds<=1800:parser.error('seconds must be 1..1800')
    if not .05<=args.interval<=2:parser.error('interval must be 0.05..2')
    if args.output.exists():parser.error('Trace exists; choose a new name')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();deadline=started+args.seconds;records=[];last=None;faults=0
    with args.output.open('x',encoding='utf-8') as output:
        while time.monotonic()<deadline:
            tick=time.monotonic()
            try:
                with urllib.request.urlopen(args.base+'/status',timeout=3) as response:
                    status=json.load(response)
                record=make_record(status,args.label)
                key=(record['frame_id'],record['event_id'])
                if key!=last:
                    output.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
                    output.flush();records.append(record);last=key
            except Exception:
                faults+=1
            time.sleep(max(0,args.interval-(time.monotonic()-tick)))
    summary=summarise(records,time.monotonic()-started);summary['faults']=faults
    print(json.dumps(summary,ensure_ascii=False))
    raise SystemExit(1 if faults else 0)


if __name__=='__main__':main()
