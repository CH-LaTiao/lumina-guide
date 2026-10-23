"""Guided distance-truth collection against the running preview.

The operator measures a target with a tape, enters that distance, and the tool
records the camera's centre range plus depth coverage for the same moment.
Output is a UTF-8 CSV plus a printed summary (MAE / P95 absolute error).
"""
import argparse
import csv
import json
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIELDS=('recorded_at','tag','tape_distance_m','center_range_m','abs_error_m',
    'depth_valid_ratio','frame_age_ms','frame_id')


def read_status(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/status',timeout=3) as response:
        return json.load(response)


def take_row(status,tag,tape):
    center=status.get('range_center_m')
    return {'recorded_at':datetime.now().astimezone().isoformat(timespec='seconds'),
        'tag':tag,'tape_distance_m':round(float(tape),3),
        'center_range_m':None if center is None else round(float(center),3),
        'abs_error_m':None if center is None else round(abs(float(tape)-float(center)),3),
        'depth_valid_ratio':round(float(status.get('valid_depth_ratio') or 0),3),
        'frame_age_ms':status.get('frame_age_ms'),'frame_id':status.get('frame_id')}


def summarise(rows):
    errors=[row['abs_error_m'] for row in rows if row.get('abs_error_m') is not None]
    missing=len(rows)-len(errors)
    if not errors:
        return {'trials':len(rows),'valid':0,'missing':missing,'mae_m':None,'p95_m':None,'max_m':None}
    ordered=sorted(errors)
    p95=ordered[min(len(ordered)-1,round(.95*(len(ordered)-1)))]
    return {'trials':len(rows),'valid':len(errors),'missing':missing,
        'mae_m':round(statistics.fmean(errors),3),'p95_m':round(p95,3),'max_m':round(max(errors),3)}


def append_rows(path,rows):
    new=not path.exists()
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS)
        if new:writer.writeheader()
        for row in rows:writer.writerow(row)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--tag',default='general')
    parser.add_argument('--out',default=str(ROOT/'runtime'/'field-measure.csv'))
    parser.add_argument('--once',type=float,help='record one sample at this tape distance and exit')
    parser.add_argument('--settle',type=float,default=0.5,help='seconds to wait before sampling')
    args=parser.parse_args()
    path=Path(args.out);rows=[]
    if args.once is not None:
        time.sleep(max(0,args.settle))
        rows.append(take_row(read_status(args.port),args.tag,args.once))
    else:
        print('输入卷尺距离（米），回车结束。每次输入前请把目标放稳并等画面更新。')
        while True:
            try:text=input('距离(m): ').strip()
            except EOFError:break
            if not text:break
            try:tape=float(text)
            except ValueError:print('请输入数字');continue
            time.sleep(max(0,args.settle))
            rows.append(take_row(read_status(args.port),args.tag,tape))
            row=rows[-1]
            print(f"  相机 {row['center_range_m']} m / 误差 {row['abs_error_m']} m / 深度覆盖 {row['depth_valid_ratio']:.0%}")
    if not rows:raise SystemExit('没有记录任何数据')
    append_rows(path,rows)
    print(json.dumps(summarise(rows),ensure_ascii=False))
    print(f'已写入 {path}')


if __name__=='__main__':main()
