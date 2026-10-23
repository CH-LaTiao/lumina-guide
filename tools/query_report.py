"""Summarise recorded online-query outcomes from runtime/queries.sqlite3."""
import argparse
import json
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(path):
    connection=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    try:rows=connection.execute('SELECT created_utc,scene,result FROM queries ORDER BY created_utc').fetchall()
    finally:connection.close()
    parsed=[]
    for created,scene,result in rows:
        try:data=json.loads(result)
        except (TypeError,ValueError):data={'state':'UNPARSED'}
        parsed.append({'created':created,'scene':scene,'result':data})
    return parsed

def summarise(records):
    states=Counter();scenes=Counter();flags=Counter()
    kinds=Counter();observations=[];missing=[]
    elapsed=[];normalised=0;reviewed=0
    for record in records:
        result=record.get('result') or {}
        states[result.get('state','?')]+=1
        scenes[record.get('scene') or result.get('scene') or '?']+=1
        plan=result.get('plan')
        if isinstance(plan,dict):
            kinds[(plan.get('next_step') or {}).get('kind','?')]+=1
            observations.append(len(plan.get('observations') or []))
            missing.append(len(plan.get('missing_information') or []))
        if isinstance(result.get('elapsed_ms'),(int,float)):elapsed.append(result['elapsed_ms'])
        if result.get('ids_normalized'):normalised+=1
        if result.get('review'):reviewed+=1
        for flag in result.get('safety_flags') or []:
            if isinstance(flag,dict):flags[flag.get('code','?')]+=1
    def pct(values,share):
        if not values:return None
        ordered=sorted(values);return round(ordered[min(len(ordered)-1,int(round(share*(len(ordered)-1))))],1)
    return {'total':len(records),'states':dict(states),'scenes':dict(scenes),
        'next_step_kinds':dict(kinds),
        'observations_median':round(statistics.median(observations),1) if observations else None,
        'missing_information_median':round(statistics.median(missing),1) if missing else None,
        'elapsed_ms':{'median':round(statistics.median(elapsed),1) if elapsed else None,
            'p95':pct(elapsed,.95),'max':round(max(elapsed),1) if elapsed else None},
        'ids_normalized':normalised,'review':reviewed,'safety_flags':dict(flags)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'runtime/queries.sqlite3'))
    parser.add_argument('--json',action='store_true');args=parser.parse_args()
    records=load(args.db);report=summarise(records)
    if args.json:print(json.dumps(report,ensure_ascii=False,indent=1));return
    print(f"记录 {report['total']} 条；状态 {report['states']}；场景 {report['scenes']}")
    print(f"用时中位数 {report['elapsed_ms']['median']} ms，P95 {report['elapsed_ms']['p95']} ms，最大 {report['elapsed_ms']['max']} ms")
    print(f"ID被客户端重写 {report['ids_normalized']} 条；审阅模式 {report['review']} 条；安全提示 {report['safety_flags']}")
    print(f"下一动作 {report['next_step_kinds']}；观察项中位 {report['observations_median']}；缺失信息中位 {report['missing_information_median']}")

if __name__=='__main__':main()
