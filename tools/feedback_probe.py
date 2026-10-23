"""Inspect real server feedback and guidance coherence; no shadow re-simulation."""
import argparse
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path


def levels(info):
    rows=info.get('native_depth') or []
    if len(rows)!=3:return None
    values=[row.get('level') for row in rows]
    return values if all(type(value) is int and 0<=value<=3 for value in values) else None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='http://127.0.0.1:8765')
    parser.add_argument('--seconds',type=float,default=20)
    parser.add_argument('--interval',type=float,default=.1)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--expect-version')
    args=parser.parse_args()
    if args.output.exists():parser.error('Evidence exists; choose a new name')
    samples=[];kinds=[];availability=[];failures=0;errors=[];events=[]
    baseline_event_id=None;observed_events={}
    started=time.monotonic();deadline=started+args.seconds
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(args.base+'/status',timeout=3) as response:
                info=json.load(response)
            state=info['feedback'];guidance=info['guidance'];events=state.get('events') or []
            event_ids=[event.get('event_id') for event in events if isinstance(event.get('event_id'),int)]
            if baseline_event_id is None:baseline_event_id=max(event_ids,default=0)
            for event in events:
                event_id=event.get('event_id')
                if isinstance(event_id,int) and event_id>baseline_event_id:observed_events[event_id]=event
            speech=state.get('speech') or {}
            if args.expect_version and info.get('software_version')!=args.expect_version:errors.append('WRONG_VERSION')
            if not state.get('paused') and guidance.get('mode')!='PAUSED':
                if (speech.get('text') or '')!=(guidance.get('speech') or ''):errors.append('SPEECH_MISMATCH')
                if speech and speech.get('guidance_event_id')!=guidance.get('event_id'):errors.append('EVENT_MISMATCH')
            if not guidance.get('fresh') and any(r.get('active') for r in state['haptics']):errors.append('STALE_HAPTICS')
            if state.get('real_output') is not False:errors.append('PHYSICAL_OUTPUT_ENABLED')
            kind=speech.get('kind')
            kinds.append(kind)
            availability.append('FRESH' if speech.get('fresh') is True else ('STALE' if speech else 'NO_GUIDANCE'))
            samples.append({'t':round(time.monotonic()-started,2),'frame_age_ms':info.get('frame_age_ms'),
                'levels':levels(info),'kind':kind,'text':speech.get('text'),
                'direction':speech.get('direction'),
                'guidance_event_id':speech.get('guidance_event_id'),'fresh':speech.get('fresh'),
                'channels':[row['channel'] for row in state['haptics'] if row['active']]})
        except Exception:
            failures+=1
        time.sleep(args.interval)
    transitions=sum(left!=right for left,right in zip(kinds,kinds[1:]))
    unavailable_flips=sum(left!=right for left,right in zip(availability,availability[1:])
        if left!='NO_GUIDANCE' and right!='NO_GUIDANCE')
    counts=Counter(value or 'NONE' for value in kinds)
    new_events=[observed_events[key] for key in sorted(observed_events)]
    report={'mode':'LIVE_SHARED_GUIDANCE','samples':len(samples),'poll_failures':failures,
        'seconds':args.seconds,'state_counts':dict(counts),'kind_transitions':transitions,
        'unavailable_flips':unavailable_flips,'availability_states':dict(Counter(availability)),'events':len(new_events),
        'event_kinds':[event.get('kind') for event in new_events],
        'baseline_event_id':baseline_event_id,'cumulative_events':len(events),
        'consistency_errors':dict(Counter(errors)),
        'checks':{'samples':len(samples)>0,'no_poll_failures':failures==0,
            'no_availability_flapping':unavailable_flips<=1,'coherent_outputs':not errors},
        'samples_detail':samples,'images_saved':0,'cloud_calls':0,'real_output':False}
    report['passed']=all(report['checks'].values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('passed','samples','state_counts',
        'kind_transitions','unavailable_flips','events','poll_failures')},ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
