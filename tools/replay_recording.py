"""Offline integrity and depth-coverage report; no camera, cloud, audio or motor output."""
import argparse
import json
import struct
from recording import replay

def summarize(path):
    count = 0; fractions = []; first = None; last = None
    for meta, depth, _ in replay(path):
        nonzero = sum(v[0] != 0 for v in struct.iter_unpack('<H', depth))
        fractions.append(nonzero/(meta['width']*meta['height']))
        first = meta['host_mono_ns'] if first is None else first
        last = meta['host_mono_ns']; count += 1
    return {'mode': 'offline_replay', 'real_output': False, 'frames': count,
            'capture_span_s': (last-first)/1e9 if count else 0,
            'mean_nonzero_depth_ratio': sum(fractions)/count if count else None,
            'scope': 'coverage only; not distance accuracy or obstacle validation'}

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('recording'); a = p.parse_args()
    print(json.dumps(summarize(a.recording), ensure_ascii=False, indent=2))
