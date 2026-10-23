"""Reproduce status/image races with delayed HTTP requests; never saves camera images."""
import argparse,hashlib,json,time,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import quote

def check(base,count=12,delay=.18):
    def state():
        with urllib.request.urlopen(base+'/status',timeout=3) as response:return json.load(response)
    def frame(frame_id):
        with urllib.request.urlopen(base+'/detection.jpg?frame_id='+quote(frame_id),timeout=3) as response:
            body=response.read();reported=response.headers['X-Lumina-Frame-Id'];age=float(response.headers['X-Lumina-Frame-Age-Ms'])
        assert reported==frame_id and 0<=age<=2000
        assert body.startswith(b'\xff\xd8') and body.endswith(b'\xff\xd9')
        return hashlib.sha256(body).hexdigest(),age
    samples=[]
    for _ in range(count):
        s=state();assert s['state']=='STREAMING' and s['detection']['status']=='READY'
        requested=s['detection']['frame_id'];time.sleep(delay)
        sha,age=frame(requested)
        assert frame(requested)[0]==sha
        samples.append({'frame_id':requested,'age_ms':age,'same_frame_repeat':True})
    # A previously valid frame must expire, rather than fall back to the newest JPEG.
    time.sleep(2.05)
    for requested in (samples[-1]['frame_id'],'unknown-frame'):
        try:frame(requested)
        except urllib.error.HTTPError as exc:assert exc.code==503
        else:raise AssertionError('Expired or unknown frame accepted')
    return {'passed':True,'test':'delayed_exact_detection_frame','requests':count,
            'delay_seconds':delay,'successful':len(samples),'samples':samples,'expired_rejected':True,
            'unknown_rejected':True,'images_saved':False,'cloud_calls':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8765');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('Evidence exists; use a new filename')
    try:result=check(a.base)
    except Exception as exc:
        with a.output.open('x',encoding='utf-8') as f:json.dump({'passed':False,'error':type(exc).__name__},f)
        raise
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='samples'}))
