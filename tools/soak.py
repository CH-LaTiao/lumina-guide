"""Bounded soak monitor for the local preview service.

Samples /status and the service RSS, then writes a JSON summary. It never
starts or stops the camera; the preview must already be running.
"""
import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def read_status(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/status',timeout=3) as response:
        return json.load(response)

def rss_mb(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return round(int(line.split()[1])/1024,1)
    except OSError:pass
    return None

def read_pid(path):
    try:text=Path(path).read_text().strip()
    except OSError:return None
    if not text:return None
    try:
        if text.startswith('{'):return int(json.loads(text).get('pid') or 0) or None
        return int(text)
    except (ValueError,TypeError):return None

def pct(values,share):
    if not values:return None
    ordered=sorted(values);index=min(len(ordered)-1,int(round(share*(len(ordered)-1))))
    return ordered[index]

def summarise(samples,faults):
    def pick(key):
        return [row[key] for row in samples if isinstance(row.get(key),(int,float))]
    def block(key):
        values=pick(key)
        if not values:return None
        return {'median':round(statistics.median(values),1),'p95':round(pct(values,.95),1),
            'min':round(min(values),1),'max':round(max(values),1),'n':len(values)}
    states={}
    for row in samples:states[row.get('state','?')]=states.get(row.get('state','?'),0)+1
    rss=pick('rss_mb')
    return {'samples':len(samples),'faults':len(faults),'states':states,
        'capture_pairs_per_s':block('capture_pairs_per_s'),'frame_age_ms':block('frame_age_ms'),
        'native_depth_ms':block('native_depth_ms'),'geometry_ms':block('geometry_ms'),
        'detection_ms':block('detection_ms'),'preview_fps':block('preview_fps'),
        'imu_age_ms':block('imu_age_ms'),'tts_cache_phrases':block('tts_cache_phrases'),
        'rss_mb':{'start':rss[0] if rss else None,'end':rss[-1] if rss else None,'max':max(rss) if rss else None},
        'providers':sorted({row.get('provider') for row in samples if row.get('provider')}),
        'gait_states':{state:sum(1 for row in samples if row.get('gait_state')==state)
            for state in sorted({row.get('gait_state') for row in samples if row.get('gait_state')})},
        'modes':{mode:sum(1 for row in samples if row.get('mode')==mode)
            for mode in sorted({row.get('mode') for row in samples if row.get('mode')})},
        'tts_models':sorted({row.get('tts_model') for row in samples if row.get('tts_model')}),
        'ir_available':sum(1 for row in samples if row.get('ir')),
        'online_states':sorted({row.get('online_state') for row in samples if row.get('online_state')})}


def write_report(path,started_utc,minutes,interval,samples,faults,final=False):
    report={'started_utc':started_utc,'minutes':minutes,'interval_s':interval,
        'final':final,'summary':summarise(samples,faults),'faults':faults[:50],'samples':samples}
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix(target.suffix+'.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=1),encoding='utf-8')
    temporary.replace(target)
    return report

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--minutes',type=float,default=30);parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--port',type=int,default=8765);parser.add_argument('--output',required=True)
    parser.add_argument('--pid-file',default=str(ROOT/'runtime/preview.pid'))
    args=parser.parse_args()
    started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());started=time.monotonic()
    deadline=started+args.minutes*60;samples=[];faults=[];last_preview=None
    service_pid=read_pid(args.pid_file)
    while time.monotonic()<deadline:
        tick=time.monotonic()
        try:
            status=read_status(args.port);detection=status.get('detection') or {};geometry=status.get('geometry') or {}
            guidance=status.get('guidance') or {};imu=status.get('imu') or {};audio=status.get('audio') or {}
            preview=status.get('preview_frames')
            rate=None
            if last_preview is not None and preview is not None and preview>=last_preview:
                rate=round((preview-last_preview)/max(1e-6,tick-last_tick),1)
            last_preview=preview;last_tick=tick
            samples.append({'t':round(tick-started,1),'state':status.get('state'),
                'capture_pairs_per_s':status.get('capture_pairs_per_s'),'frame_age_ms':status.get('frame_age_ms'),
                'native_depth_ms':status.get('native_depth_ms'),'geometry_ms':geometry.get('compute_ms'),
                'detection_ms':detection.get('inference_ms'),'provider':detection.get('provider'),
                'preview_fps':rate,'ir':bool((status.get('ir') or {}).get('available')),
                'mode':guidance.get('mode'),'requested_mode':guidance.get('requested_mode'),
                'gait_state':imu.get('state'),'imu_age_ms':imu.get('age_ms'),
                'gait_profile':imu.get('profile_source'),'tts_model':audio.get('tts_model'),
                'tts_state':audio.get('tts_state'),'tts_cache_phrases':audio.get('cache_phrases'),
                'online_state':(status.get('online') or {}).get('state'),'rss_mb':rss_mb(service_pid) if service_pid else None})
        except Exception as exc:
            faults.append({'t':round(time.monotonic()-started,1),'error':type(exc).__name__+': '+str(exc)[:120]})
        if len(samples)%15==0:
            write_report(args.output,started_utc,args.minutes,args.interval,samples,faults)
        time.sleep(max(0,args.interval-(time.monotonic()-tick)))
    report=write_report(args.output,started_utc,args.minutes,args.interval,samples,faults,final=True)
    summary=report['summary']
    print(json.dumps({'samples':summary['samples'],'faults':summary['faults'],'states':summary['states'],
        'preview_fps':summary['preview_fps'],'gait_states':summary['gait_states'],
        'imu_age_ms':summary['imu_age_ms'],'tts_models':summary['tts_models'],
        'tts_cache_phrases':summary['tts_cache_phrases'],'rss_mb':summary['rss_mb']},ensure_ascii=False))
    bad_states=sum(count for state,count in summary['states'].items() if state not in ('STREAMING','WARMING'))
    raise SystemExit(1 if faults or bad_states else 0)

if __name__=='__main__':main()
