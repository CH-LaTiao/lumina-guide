"""Short live HTTP acceptance probe. No cloud requests and no camera images saved."""
import argparse,json,statistics,time,urllib.request,urllib.error
from pathlib import Path

def check(base,seconds,expect_camera_unavailable=False):
    def get(path):
        with urllib.request.urlopen(base+path,timeout=3) as response:return response.read()
    def post(path,body,origin=True):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=base
        req=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)
    samples=[];states=[];deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        s=json.loads(get('/status'))
        readiness=s.get('readiness') or {}
        assert readiness.get('stage') and readiness.get('ready_for_movement') is False
        states.append({'state':s['state'],'attempt':s.get('reconnect_attempt')})
        if expect_camera_unavailable:
            assert s['state'] in ('CONNECTING','RECONNECTING')
            assert s['detection']['status']=='CAMERA_UNAVAILABLE'
            assert not any(x['active'] for x in s['feedback']['haptics'])
        if s['state']=='STREAMING':
            samples.append({'age_ms':s['frame_age_ms'],'capture_fps':s['capture_pairs_per_s'],
                'coverage':s['valid_depth_ratio'],'native_ms':s['native_depth_ms'],
                'detector':s['detection'].get('provider'),'inference_ms':s['detection'].get('inference_ms'),
                'frame_id':s['frame_id'],'physical_output':s['feedback']['real_output']})
        time.sleep(.25)
    if not expect_camera_unavailable and len(samples)<max(5,seconds*2):raise AssertionError('Too few fresh live samples')
    assert all(not x['physical_output'] for x in samples)
    if not expect_camera_unavailable:assert len(set(x['frame_id'] for x in samples))>5
    # UI resources and frame age/ID matching endpoints.
    assert b'live_view.js' in get('/')
    assert b'feedback/pause' in get('/live_view.js')
    if expect_camera_unavailable:
        try:get('/frame.jpg')
        except urllib.error.HTTPError as e:assert e.code==503
        else:raise AssertionError('Old camera frame leaked')
        attempts=[x['attempt'] for x in states if x['attempt'] is not None]
        assert attempts and max(attempts)>min(attempts)
    else:assert get('/frame.jpg').startswith(b'\xff\xd8')
    try:get('/detection.jpg?frame_id=wrong')
    except urllib.error.HTTPError as e:assert e.code==503
    else:raise AssertionError('Wrong-frame request was accepted')
    original=json.loads(get('/status'))['feedback']['paused']
    try:
        s=post('/feedback/pause',{'paused':True});assert s['paused'] and s['speech'] is None
        time.sleep(.4);s=json.loads(get('/status'))['feedback'];assert s['paused'] and not any(x['active'] for x in s['haptics'])
        s=post('/feedback/pause',{'paused':False});assert not s['paused']
        try:post('/feedback/pause',{'paused':True},False)
        except urllib.error.HTTPError as e:assert e.code==403
        else:raise AssertionError('Cross-origin control accepted')
    finally:post('/feedback/pause',{'paused':original})
    csv=get('/reviews.csv');assert csv.startswith(b'\xef\xbb\xbfrequest_id,source_sha256,base_revision')
    req=urllib.request.Request(base+'/reviews/import',data=csv,headers={'Origin':base,'Content-Type':'text/csv'})
    with urllib.request.urlopen(req,timeout=3) as response:assert json.load(response)['imported']==0
    assert json.loads(get('/reviews/candidates'))['status']=='DRAFT_NOT_ACTIVE'
    fields=('age_ms','capture_fps','coverage','native_ms','inference_ms')
    return {'test':'camera_absent_http_probe' if expect_camera_unavailable else 'real_camera_http_short_probe','passed':True,'duration_s':seconds,'samples':len(samples),
        'states':sorted({x['state'] for x in states}),
        'providers':sorted({s['detector'] for s in samples if s['detector']}),
        'stats':{k:{'min':min(v),'median':statistics.median(v),'max':max(v)}
                 for k in fields if (v:=[s[k] for s in samples if s[k] is not None])},
        'checks':(['absent camera retries','no stale frame','haptics zero'] if expect_camera_unavailable else ['live frames','fresh frame IDs'])+['wrong frame rejected','pause/resume','cross-origin rejected','CSV unchanged round-trip','draft candidates'],
        'cloud_calls':0,'real_output':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8765');p.add_argument('--seconds',type=int,default=15);p.add_argument('--output',type=Path,required=True);p.add_argument('--expect-camera-unavailable',action='store_true');a=p.parse_args()
    if not 3<=a.seconds<=60:p.error('seconds must be 3..60')
    if a.output.exists():p.error('Evidence file exists; choose a new name')
    try:result=check(a.base,a.seconds,a.expect_camera_unavailable)
    except Exception as exc:
        with a.output.open('x',encoding='utf-8') as f:json.dump({'passed':False,'error_type':type(exc).__name__,'message':str(exc)},f,ensure_ascii=False,indent=2)
        raise
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
