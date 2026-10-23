import csv
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prompt_registry import PromptRegistry, PROMPTS
from review_store import FIELDS
from online_query import OnlineQuery
from voice_io import answer_text


class PromptWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'queries.sqlite3'
        self.prompts=self.root/'prompts';shutil.copytree(PROMPTS,self.prompts)
        self.store=PromptRegistry(self.path,self.prompts)
        db=self.store.connect()
        with db:db.execute('INSERT INTO queries VALUES(?,?,?,?,?,?,?)',('original','2026-10-18','find_target','厕所在哪？','original prompt','original answer','{}'))
        db.close()
        rows=list(csv.DictReader(io.StringIO(self.store.export_csv().decode('utf-8-sig'))))
        rows[0].update(corrected_prompt='优先核对厕所标识和入口，不能仅凭相似门形推断。',corrected_answer='需要读清标识，再确认入口。',review_note='厕所场景增强')
        buf=io.StringIO(newline='');w=csv.DictWriter(buf,FIELDS);w.writeheader();w.writerows(rows)
        self.store.import_csv(buf.getvalue().encode('utf-8-sig'));self.store.sync_candidates()
        self.version=self.store.status()['versions'][0]['version']
        self.human={'scene_correct':True,'answers_question':True,'next_step_useful':True}

    def run_trial(self,trial=True,unsafe=False):
        query=OnlineQuery(self.path);query.prompts=self.store;query.generation=1
        seen={}
        def response(req,timeout):
            body=json.loads(req.data);context=json.loads(body['messages'][1]['content'][0]['text']);seen.update(context)
            seen['system_prompt']=body['messages'][0]['content']
            plan=json.loads((PROMPTS/'plan.example.json').read_text(encoding='utf-8'))
            plan.update(request_id=context['request_id'],snapshot_id=context['snapshot_id'],scene_id=context['scene_id'])
            plan['observations']=[{'fact':'标识尚未读清','evidence_ids':context['evidence_ids']}]
            plan['next_step'].update(kind='observe',say='前方可以安全通行' if unsafe else '请先确认标识文字。',evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        rid=str(uuid.uuid4());bundle=self.store.bundle('find_target',self.version,trial=True) if trial else None
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=response):
            query.run(rid,1,'厕所在哪？','find_target',{'captured':time.monotonic(),'jpeg':b'synthetic-test-fixture','frame_id':'test:1'},
                review=trial,budget=30,prompt_bundle=bundle,trial=trial)
        return query,rid,seen

    def approve(self):
        query,rid,_=self.run_trial()
        self.assertEqual(query.read()['state'],'CANDIDATE',query.read())
        self.store.assess_trial(rid,True,self.human,'按测试夹具核对通过')
        return rid

    def test_full_csv_trial_review_publish_query_and_rollback(self):
        self.assertEqual(self.store.sync_candidates()['created'],0)
        self.assertEqual(self.store.bundle('find_target')['version'],'builtin:find_target')
        with self.assertRaises(ValueError):self.store.activate('find_target',self.version,0,'尚未试答')
        rid=self.approve();result=self.store.activate('find_target',self.version,0,'采用人工修订')
        self.assertEqual(result['generation'],1)
        query,_,seen=self.run_trial(trial=False)
        self.assertEqual(query.read()['prompt_version'],self.version)
        self.assertIn('优先核对厕所标识',seen['system_prompt'])
        self.assertIn((PROMPTS/'base.txt').read_text(encoding='utf-8'),seen['system_prompt'])
        self.assertIn('JSON Schema:',seen['system_prompt'])
        self.assertEqual(seen['template_sha256'],query.read()['prompt_sha256'])
        reopened=PromptRegistry(self.path,self.prompts)
        self.assertEqual(reopened.bundle('find_target')['version'],self.version)
        reopened.activate('find_target',None,1,'回滚验证',rollback=True)
        query,_,seen=self.run_trial(trial=False)
        self.assertEqual(query.read()['prompt_version'],'builtin:find_target')
        self.assertNotIn('优先核对厕所标识',seen['system_prompt'])
        self.assertEqual(self.store.bundle('metro')['version'],'builtin:metro')
        rows=list(csv.DictReader(io.StringIO(self.store.export_report().decode('utf-8-sig'))))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['request_id'],rid)
        self.assertEqual(rows[0]['latest_assessment'],'PASS')
        self.assertIn('优先核对厕所标识',rows[0]['actual_prompt'])

    def test_unsafe_or_incomplete_human_review_cannot_publish(self):
        _,rid,_=self.run_trial(unsafe=True)
        with self.assertRaises(ValueError):self.store.assess_trial(rid,True,self.human,'不能通过')
        result=self.store.assess_trial(rid,False,self.human,'存在未经核验的通行表述')
        self.assertFalse(result['accepted'])
        with self.assertRaises(ValueError):self.store.activate('find_target',self.version,0,'拒绝启用')
        _,rid,_=self.run_trial()
        with self.assertRaises(ValueError):self.store.assess_trial(rid,True,{**self.human,'scene_correct':False},'未核对现场')

    def test_optimistic_publish_and_scene_binding(self):
        self.approve();self.store.activate('find_target',self.version,0,'首次发布')
        with self.assertRaises(ValueError):self.store.activate('find_target',None,0,'过期页面')
        with self.assertRaises(ValueError):self.store.activate('metro',self.version,0,'错误场景')
        with self.assertRaises(ValueError):self.store.bundle('metro',self.version,trial=True)
        self.assertEqual(self.store.bundle('find_target')['version'],self.version)

    def test_changed_base_or_withdrawn_assessment_requires_retest(self):
        rid=self.approve();self.store.activate('find_target',self.version,0,'启用')
        self.store.assess_trial(rid,False,self.human,'复核后撤销通过')
        bundle=self.store.bundle('find_target')
        self.assertEqual(bundle['version'],'builtin:find_target');self.assertIsNotNone(bundle['fallback_reason'])
        self.store.assess_trial(rid,True,self.human,'再次人工核对')
        self.assertEqual(self.store.bundle('find_target')['version'],self.version)
        with (self.prompts/'base.txt').open('a',encoding='utf-8') as f:f.write('\n新共用约束。')
        self.assertEqual(self.store.bundle('find_target')['version'],'builtin:find_target')
        with self.assertRaises(ValueError):self.store.assess_trial(rid,True,self.human,'不能复用旧评测')

    def test_trial_never_speaks_or_persists_task_visual_plan(self):
        query,_,_=self.run_trial()
        self.assertIsNone(query.task['last_plan'])
        self.assertTrue(query.read()['prompt_trial'])
        with self.assertRaises(ValueError):answer_text(query.read(),'STANDING')

    def test_assessment_idempotent_and_original_unchanged(self):
        rid=self.approve();result=self.store.assess_trial(rid,True,self.human,'按测试夹具核对通过')
        self.assertTrue(result['skipped'])
        db=self.store.connect()
        row=db.execute('SELECT * FROM queries WHERE request_id="original"').fetchone()
        self.assertEqual(row['prompt'],'original prompt');self.assertEqual(row['raw_response'],'original answer')
        self.assertEqual(db.execute('SELECT count(*) FROM prompt_assessments').fetchone()[0],1);db.close()

    def test_pin_version_before_worker_and_trial_does_not_consume_task_rounds(self):
        self.approve();self.store.activate('find_target',self.version,0,'启用')
        query=OnlineQuery(self.path);query.prompts=self.store
        with patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.threading.Timer'),patch('online_query.threading.Thread') as worker:
            query.submit('厕所在哪？','find_target',{'captured':time.monotonic()},True,prompt_version=self.version)
            pinned=worker.call_args.kwargs['kwargs']['prompt_bundle']
            self.assertEqual(query.task['rounds'],0);query.cancel()
        self.store.activate('find_target',None,1,'恢复内置')
        self.assertEqual(pinned['version'],self.version)
        self.assertEqual(self.store.bundle('find_target')['version'],'builtin:find_target')


if __name__=='__main__':unittest.main()
