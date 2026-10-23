"""Summarise and audit one JSONL route trace without modifying it."""
import argparse
import json
from collections import Counter
from pathlib import Path


def load(path):
    records=[]
    for number,line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        try:value=json.loads(line)
        except json.JSONDecodeError as exc:raise ValueError(f'Invalid JSON at line {number}') from exc
        if not isinstance(value,dict):raise ValueError(f'Line {number} is not an object')
        records.append(value)
    if not records:raise ValueError('Trace is empty')
    return records


def _counts(records,key):
    return dict(sorted(Counter(row.get(key) for row in records).items(),key=lambda item:str(item[0])))


def report(records):
    if not records:raise ValueError('Trace is empty')
    times=[row['wall_time'] for row in records if isinstance(row.get('wall_time'),(int,float))]
    duration=max(times)-min(times) if len(times)>1 else 0.
    versions={row.get('software_version') for row in records if row.get('software_version')}
    commits={row.get('software_commit') for row in records if row.get('software_commit')}
    speeches=[];previous=None
    for row in sorted(records,key=lambda item:item.get('wall_time',0)):
        speech=row.get('speech')
        if speech and speech!=previous:speeches.append(speech)
        previous=speech
    movement_true=sum(row.get('movement_allowed') is not False for row in records)
    states=_counts(records,'state')
    checks={
        'movement_locked':movement_true==0,
        'single_software_version':len(versions)<=1,
        'software_provenance':bool(versions and commits),
        'streaming_or_warming_only':all(state in ('STREAMING','WARMING') for state in states),
    }
    return {
        'records':len(records),'duration_s':round(duration,1),
        'labels':sorted({row.get('label') for row in records if row.get('label')}),
        'software_versions':sorted(v for v in versions if v),'software_commits':sorted(c for c in commits if c),
        'sessions':len({row.get('session_id') for row in records if row.get('session_id')}),
        'frames':len({row.get('frame_id') for row in records if row.get('frame_id')}),
        'states':states,'actions':_counts(records,'action'),
        'traffic_decisions':_counts(records,'traffic_decision'),
        'stages':_counts(records,'readiness_stage'),
        'direction_frames':_counts(records,'direction_frame'),'body_calibration':_counts(records,'body_calibration'),
        'gait_states':_counts(records,'gait_state'),'mode_sources':_counts(records,'mode_source'),
        'motion_risks':sum(bool(row.get('motion_risk')) for row in records),
        'detours':sum(bool(row.get('detour')) for row in records),
        'cross_modal_hazards':sum(bool(row.get('cross_modal_hazards')) for row in records),
        'speech_transitions':len(speeches),'speeches':speeches[:30],
        'movement_true_records':movement_true,'checks':checks,'passed':all(checks.values()),
        'images_saved':0,'cloud_calls':0,
    }


def main():
    parser=argparse.ArgumentParser();parser.add_argument('trace',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():parser.error('Report exists; choose a new name')
    value=report(load(args.trace))
    text=json.dumps(value,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
    raise SystemExit(0 if value['passed'] else 1)


if __name__=='__main__':main()
