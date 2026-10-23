"""Mixed-load soak: status sampling, periodic frame pulls and online queries.

Runs against the live preview only. Online queries use review mode (30s budget)
and never actuate; each request still requires explicit consent in the payload.
"""
import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from soak import summarise


def get_status(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/status',timeout=5) as response:
        return json.load(response)


def pull_frame(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/frame.jpg',timeout=5) as response:
        return len(response.read())


def submit_query(port,question,scene):
    body=json.dumps({'question':question,'scene':scene,'consent':True,'review':True}).encode()
    request=urllib.request.Request(f'http://127.0.0.1:{port}/query',data=body,
        headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
    with urllib.request.urlopen(request,timeout=5) as response:
        return json.load(response).get('request_id')


def wait_query(port,deadline=45):
    started=time.monotonic()
    while time.monotonic()-started<deadline:
        online=get_status(port).get('online') or {}
        if online.get('state') not in ('RUNNING',None):return online
        time.sleep(1)
    return {'state':'LOCAL_WAIT_TIMEOUT'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--minutes',type=float,default=120)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--sample-every',type=float,default=5)
    parser.add_argument('--frame-every',type=float,default=30)
    parser.add_argument('--query-every-min',type=float,default=10)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    started=time.monotonic();deadline=started+args.minutes*60
    started_utc=datetime.now(timezone.utc).isoformat(timespec='seconds')
    samples=[];faults=[];queries=[];frame_ok=0;frame_fail=0
    next_frame=started;next_query=started+60*args.query_every_min;query_index=0
    target=Path(args.out)
    while time.monotonic()<deadline:
        tick=time.monotonic()
        try:
            status=get_status(args.port);detection=status.get('detection') or {};geometry=status.get('geometry') or {}
            samples.append({'t':round(tick-started,1),'state':status.get('state'),
                'capture_pairs_per_s':status.get('capture_pairs_per_s'),'frame_age_ms':status.get('frame_age_ms'),
                'geometry_ms':geometry.get('compute_ms'),'detection_ms':detection.get('inference_ms'),
                'provider':detection.get('provider'),'online_state':(status.get('online') or {}).get('state')})
        except Exception as exc:
            faults.append({'t':round(time.monotonic()-started,1),'error':type(exc).__name__+': '+str(exc)[:100]})
        if time.monotonic()>=next_frame:
            next_frame=time.monotonic()+args.frame_every
            try:pull_frame(args.port);frame_ok+=1
            except Exception:frame_fail+=1
        if time.monotonic()>=next_query:
            next_query=time.monotonic()+60*args.query_every_min;query_index+=1
            scene=('general','obstacle','stairs')[query_index%3]
            started_query=time.monotonic()
            try:
                request_id=submit_query(args.port,'我现在应该确认什么',scene)
                result=wait_query(args.port)
                queries.append({'t':round(time.monotonic()-started,1),'scene':scene,'request_id':request_id,
                    'state':result.get('state'),'elapsed_ms':result.get('elapsed_ms'),
                    'wall_ms':round((time.monotonic()-started_query)*1000)})
            except Exception as exc:
                queries.append({'t':round(time.monotonic()-started,1),'scene':scene,
                    'state':'REQUEST_FAILED','error':type(exc).__name__})
        if len(samples)%15==0:
            write(target,started_utc,args,samples,faults,queries,frame_ok,frame_fail,False)
        time.sleep(max(0,args.sample_every-(time.monotonic()-tick)))
    report=write(target,started_utc,args,samples,faults,queries,frame_ok,frame_fail,True)
    print(json.dumps({'samples':report['summary']['samples'],'faults':report['summary']['faults'],
        'queries':len(queries),'query_states':report['query_states'],
        'frames_ok':frame_ok,'frames_failed':frame_fail},ensure_ascii=False))


def write(target,started_utc,args,samples,faults,queries,frame_ok,frame_fail,final):
    states={}
    for query in queries:states[query.get('state')]=states.get(query.get('state'),0)+1
    report={'started_utc':started_utc,'minutes':args.minutes,'final':final,
        'summary':summarise(samples,faults),'faults':faults[:50],
        'query_states':states,'queries':queries,'frames_ok':frame_ok,'frames_failed':frame_fail,
        'samples':samples}
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix(target.suffix+'.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=1),encoding='utf-8')
    temporary.replace(target)
    return report


if __name__=='__main__':main()
