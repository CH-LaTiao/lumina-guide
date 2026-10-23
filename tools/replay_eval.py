"""Replay stored model plans through the current schema and safety rules.

Offline only: it never calls the cloud and never writes the database. The report
shows which historical plans still validate and which safety flags the current
rule set adds compared with what was stored at capture time.
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from safety_review import review

ROOT=Path(__file__).resolve().parents[1]


def load(path):
    connection=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    try:rows=connection.execute('SELECT request_id,scene,result FROM queries ORDER BY created_utc').fetchall()
    finally:connection.close()
    records=[]
    for request_id,scene,result in rows:
        try:data=json.loads(result)
        except (TypeError,ValueError):continue
        records.append({'request_id':request_id,'scene':scene,'result':data})
    return records


def evaluate(records,schema):
    validator=Draft202012Validator(schema)
    now=Counter();stored=Counter();kinds=Counter();gates=Counter()
    with_plan=0;valid=0;changed=0
    for record in records:
        result=record.get('result') or {}
        plan=result.get('plan')
        if not isinstance(plan,dict):continue
        with_plan+=1
        if not list(validator.iter_errors(plan)):valid+=1
        codes={flag.get('code') for flag in review(plan) if isinstance(flag,dict)}
        previous={flag.get('code') for flag in result.get('safety_flags') or [] if isinstance(flag,dict)}
        now.update(codes);stored.update(previous)
        if codes!=previous:changed+=1
        kinds[(plan.get('next_step') or {}).get('kind','?')]+=1
        gates[result.get('gate','?')]+=1
    return {'records':len(records),'with_plan':with_plan,'schema_valid':valid,
        'flags_now':dict(now),'flags_stored':dict(stored),'plans_with_changed_flags':changed,
        'next_step_kinds':dict(kinds),'gates':dict(gates)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default=str(ROOT/'runtime/queries.sqlite3'))
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    schema=json.loads((ROOT/'prompts/online/plan.schema.json').read_text(encoding='utf-8'))
    report=evaluate(load(args.db),schema)
    if args.json:print(json.dumps(report,ensure_ascii=False,indent=1));return
    print(f"记录 {report['records']} 条，含方案 {report['with_plan']} 条，schema 通过 {report['schema_valid']} 条")
    print(f"当前规则标记 {report['flags_now']}；当时记录 {report['flags_stored']}；结论有变化的方案 {report['plans_with_changed_flags']} 条")
    print(f"下一动作 {report['next_step_kinds']}；门控 {report['gates']}")


if __name__=='__main__':main()
