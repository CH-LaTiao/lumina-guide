"""Run the local software and live-preview release gates into one evidence bundle."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def run_step(name,command,root,timeout):
    started=time.monotonic()
    try:
        value=subprocess.run(command,cwd=root,text=True,capture_output=True,timeout=timeout)
        return {'name':name,'command':command,'returncode':value.returncode,
                'elapsed_s':round(time.monotonic()-started,2),
                'stdout':value.stdout[-4000:],'stderr':value.stderr[-2000:],
                'passed':value.returncode==0}
    except subprocess.TimeoutExpired as exc:
        return {'name':name,'command':command,'returncode':None,
                'elapsed_s':round(time.monotonic()-started,2),
                'stdout':(exc.stdout or '')[-2000:] if isinstance(exc.stdout,str) else '',
                'stderr':'timeout','passed':False}


def status_checks(value):
    software=value.get('software') or {};guidance=value.get('guidance') or {};readiness=value.get('readiness') or {}
    checks={
        'streaming':value.get('state')=='STREAMING',
        'camera_fresh':(isinstance(value.get('frame_age_ms'),(int,float))
            and 0<=value['frame_age_ms']<=200),
        'release_source':software.get('source')=='release',
        'clean_release':software.get('dirty') is False,
        'has_version':bool(software.get('version') and software.get('commit')),
        'movement_locked':guidance.get('movement_allowed') is False and readiness.get('ready_for_movement') is False,
        'readiness_present':bool(readiness.get('stage') and isinstance(readiness.get('checks'),dict)),
    }
    return {'passed':all(checks.values()),'checks':checks,'version':software.get('version'),
            'commit':software.get('commit'),'code_root':software.get('code_root'),
            'frame_age_ms':value.get('frame_age_ms'),'readiness_stage':readiness.get('stage'),
            'blockers':readiness.get('blockers') or []}


def read_fresh_status(base,timeout=3):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        with urllib.request.urlopen(base+'/status',timeout=5) as response:last=json.load(response)
        if last.get('state')=='STREAMING' and isinstance(last.get('frame_age_ms'),(int,float)) \
                and 0<=last['frame_age_ms']<=200:return last
        time.sleep(.2)
    if last is None:raise TimeoutError('No status response')
    return last


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live-seconds',type=int,default=5)
    parser.add_argument('--skip-live',action='store_true')
    parser.add_argument('--base',default='http://127.0.0.1:8765')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if not args.output.suffix:parser.error('output must include a file extension')
    if args.output.exists():parser.error('Evidence output exists; choose a new name')
    evidence=args.output.with_suffix('')
    if evidence.exists():parser.error('Evidence directory exists; choose a new name')
    evidence.mkdir(parents=True)
    python=sys.executable
    steps=[]
    steps.append(run_step('python_tests',[python,'-m','pytest','-q','tests'],root,120))
    steps.append(run_step('cpp_tests',['ctest','--test-dir','build','--output-on-failure'],root,60))
    steps.append(run_step('guidance_benchmark',[python,'tools/guidance_benchmark.py',
        '--output',str(evidence/'guidance-benchmark.json')],root,60))
    steps.append(run_step('tts_benchmark',[python,'tools/tts_benchmark.py',
        '--output',str(evidence/'tts-benchmark.json')],root,60))
    live=None
    if not args.skip_live:
        steps.append(run_step('runtime_probe',[python,'tools/verify_runtime.py','--seconds',
            str(args.live_seconds),'--output',str(evidence/'runtime.json')],root,args.live_seconds+30))
        steps.append(run_step('geometry_probe',[python,'tools/verify_geometry.py','--seconds',
            str(args.live_seconds),'--output',str(evidence/'geometry.json')],root,args.live_seconds+30))
        steps.append(run_step('trace_capture',[python,'tools/route_trace.py','--seconds','3',
            '--interval','.1','--label','release-gate','--output',str(evidence/'trace.jsonl')],root,30))
        steps.append(run_step('trace_audit',[python,'tools/trace_report.py',str(evidence/'trace.jsonl'),
            '--output',str(evidence/'trace-report.json')],root,30))
        try:
            live=status_checks(read_fresh_status(args.base))
            if live.get('passed') and live.get('version') and live.get('commit'):
                tag_step=run_step('release_tag',['git','rev-parse',
                    f"refs/tags/{live['version']}^{{commit}}"],root,10)
                if tag_step['passed'] and tag_step['stdout'].strip()!=live['commit']:
                    tag_step['passed']=False
                    tag_step['stderr']='Tag commit does not match release status commit'
                steps.append(tag_step)
        except Exception as exc:
            live={'passed':False,'error':type(exc).__name__+': '+str(exc)[:200]}
    report={'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        'live':live,'steps':steps,
        'passed':all(step['passed'] for step in steps) and (live is None or live['passed']),
        'images_saved':0,'cloud_calls':0}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'steps':len(steps),'live':live},ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
