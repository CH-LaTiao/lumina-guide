"""Guided, local-only mount calibration through the preview HTTP API."""
import argparse
import json
import math
import time
import urllib.request


def number(value,name,low,high):
    if not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name} must be {low}..{high}')
    return float(value)


def make_payloads(args):
    bench={'height_m':number(args.height_m,'height',.2,2.5),
           'pitch_down_deg':number(args.pitch_down_deg,'pitch',-20,60),
           'measured':True,'stationary':True}
    body={'camera_forward_m':number(args.camera_forward_m,'camera forward',-.5,.5),
          'camera_right_m':number(args.camera_right_m,'camera right',-.5,.5),
          'yaw_deg':number(args.yaw_deg,'yaw',-45,45),
          'required_passage_width_m':number(args.required_width_m,'required width',.35,1.5),
          'measured':True,'stationary':True}
    return bench,body


def get_status(base):
    with urllib.request.urlopen(base+'/status',timeout=3) as response:
        return json.load(response)


def post(base,path,payload):
    request=urllib.request.Request(base+path,data=json.dumps(payload).encode(),
        headers={'Origin':base,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=5) as response:
        return json.load(response)


def wait_for(base,timeout,predicate):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        try:last=get_status(base)
        except Exception as exc:last={'error':type(exc).__name__, 'message':str(exc)[:100]}
        if isinstance(last,dict) and predicate(last):return last
        time.sleep(.25)
    raise TimeoutError(f'Condition not reached; last={json.dumps(last,ensure_ascii=False)[:500]}')


def calibrate(base,timeout,bench,body):
    status=get_status(base)
    if status.get('state')!='STREAMING':raise RuntimeError('Preview is not STREAMING')
    post(base,'/body/calibration',{'clear':True})
    bench_result=post(base,'/geometry/reference',bench)
    bench_sha=bench_result.get('sha256')
    if not bench_sha:raise RuntimeError('Bench reference did not return a SHA256')

    def ground_ready(value):
        geometry=value.get('geometry') or {};reference=geometry.get('reference') or {}
        return (geometry.get('ground_state')=='REFERENCE_MATCHED_CANDIDATE'
            and reference.get('source')=='BENCH' and reference.get('sha256')==bench_sha)
    wait_for(base,timeout,ground_ready)
    post(base,'/body/calibration',body)

    def body_ready(value):
        return bool((value.get('readiness') or {}).get('ready_for_body_guidance'))
    final=wait_for(base,timeout,body_ready)
    return {'state':'CALIBRATED','readiness':final['readiness'],
        'body_calibration':final['geometry']['body_calibration'],'movement_allowed':False}


def main():
    parser=argparse.ArgumentParser(description='Apply measured bench and body calibration to the local preview.')
    parser.add_argument('--base',default='http://127.0.0.1:8765')
    parser.add_argument('--height-m',type=float);parser.add_argument('--pitch-down-deg',type=float)
    parser.add_argument('--camera-forward-m',type=float);parser.add_argument('--camera-right-m',type=float)
    parser.add_argument('--yaw-deg',type=float);parser.add_argument('--required-width-m',type=float)
    parser.add_argument('--confirm-measured',action='store_true')
    parser.add_argument('--timeout',type=float,default=20)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--clear',action='store_true')
    args=parser.parse_args()
    if args.clear:
        if args.dry_run:
            print(json.dumps({'clear_body':True,'clear_bench':True}));return
        print(json.dumps({'body':post(args.base,'/body/calibration',{'clear':True}),
            'bench':post(args.base,'/geometry/reference',{'clear':True})},ensure_ascii=False))
        return
    if not args.confirm_measured:
        parser.error('--confirm-measured is required because no values are inferred')
    missing=[name for name in ('height_m','pitch_down_deg','camera_forward_m','camera_right_m',
        'yaw_deg','required_width_m') if getattr(args,name) is None]
    if missing:parser.error('missing measured values: '+', '.join(missing))
    try:bench,body=make_payloads(args)
    except ValueError as exc:parser.error(str(exc))
    if args.dry_run:
        print(json.dumps({'bench':bench,'body':body},ensure_ascii=False));return
    print(json.dumps(calibrate(args.base,args.timeout,bench,body),ensure_ascii=False))


if __name__=='__main__':main()
