"""Read-only geometry live probe; no fake installation reference or cloud images."""
import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path


def check(base, seconds):
    samples=[];camera=None;states={};frames=set();deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        with urllib.request.urlopen(base+'/status',timeout=3) as r:s=json.load(r)
        g=s.get('geometry') or {};state=g.get('state','MISSING');states[state]=states.get(state,0)+1
        readiness=s.get('readiness') or {}
        assert readiness.get('ready_for_movement') is False
        assert readiness.get('movement_released') is False
        assert isinstance(readiness.get('checks'),dict) and readiness.get('stage')
        assert not g.get('movement_allowed') and not s['real_output']
        if state=='STALE':
            assert not g['objects'] and g['plane'] is None and g['ground_state']=='UNKNOWN'
        if state=='READY':
            assert g['frame_id']==s['frame_id'] and g['diagnostic_only']
            body=g.get('body_calibration') or {}
            body_state=body if isinstance(body,str) else body.get('state','UNSET')
            assert body_state in ('UNSET','ACTIVE')
            if body_state=='ACTIVE':
                assert body.get('movement_allowed') is False
                assert all(k in body for k in ('camera_forward_m','camera_right_m','yaw_deg',
                    'required_passage_width_m'))
            c=g['counts'];assert c['sampled']==c['valid']+c['invalid']+c['unknown_near']+c['out_of_range']
            assert len(g['objects'])<=12
            if g['reference'] is None:
                assert g['removed_plane_samples']==0
                assert all(o['height_over_plane_m'] is None for o in g['objects'])
            for o in g['objects']:
                assert .3<=o['depth_z_p20_m']<=4 and o['sample_points']>=6
            samples.append({'compute_ms':g['compute_ms'],'age_ms':s['frame_age_ms'],
                            'capture_fps':s['capture_pairs_per_s'],'clusters':g['cluster_count']})
            frames.add(g['frame_id']);camera=g['camera']
        time.sleep(.2)
    assert len(samples)>=seconds*2 and len(frames)>5, 'Insufficient fresh geometry results'
    # Validate request gate without fabricating measured installation values.
    req=urllib.request.Request(base+'/geometry/reference',data=b'{}',headers={'Origin':base,'Content-Type':'application/json'})
    try:urllib.request.urlopen(req,timeout=3)
    except urllib.error.HTTPError as e:assert e.code==400
    else:raise AssertionError('Unconfirmed reference accepted')
    req=urllib.request.Request(base+'/body/calibration',data=b'{}',headers={'Origin':base,'Content-Type':'application/json'})
    try:urllib.request.urlopen(req,timeout=3)
    except urllib.error.HTTPError as e:assert e.code==400
    else:raise AssertionError('Unconfirmed body calibration accepted')
    req=urllib.request.Request(base+'/geometry/reference',data=b'{"clear":true}',headers={'Origin':'http://example.invalid'})
    try:urllib.request.urlopen(req,timeout=3)
    except urllib.error.HTTPError as e:assert e.code==403
    else:raise AssertionError('Cross-origin change accepted')
    return {'passed':True,'duration_s':seconds,'samples':len(samples),'distinct_frames':len(frames),
            'states':states,'camera':camera,'software_version':s['software_version'],
            'body_calibration':body_state,
            'stats':{k:{'min':min(v),'median':statistics.median(v),'max':max(v)}
                     for k in samples[0] if (v:=[row[k] for row in samples])},
            'physical_output':False,'cloud_calls':0,'images_saved':0,
            'limits':'Live diagnostics only. No measured ground truth or walking acceptance.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8765');p.add_argument('--seconds',type=int,default=30);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if not 3<=a.seconds<=60:p.error('seconds must be 3..60')
    if a.output.exists():p.error('Evidence exists; use a new name')
    try:result=check(a.base,a.seconds)
    except Exception as exc:
        with a.output.open('x',encoding='utf-8') as f:json.dump({'passed':False,'error':str(exc),'error_type':type(exc).__name__},f)
        raise
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result))
