"""Explicit user tasks: independent questions, bounded follow-up and trial isolation."""
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from online_query import OnlineQuery, ROOT, TASK_RESET_AFTER_S
from review_store import digest


class ImmediateWorker:
    def __init__(self,target,args=(),kwargs=None,**unused):
        self.target=target;self.args=args;self.kwargs=kwargs or {}
    def start(self):self.target(*self.args,**self.kwargs)


class OnlineTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.query=OnlineQuery(Path(self.temp.name)/'queries.sqlite3')
        self.contexts=[];self.response_mode='good'
        self.env=patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'})
        self.env.start();self.addCleanup(self.env.stop)
        self.http=patch('online_query.fetch_response',side_effect=self.response)
        self.http.start();self.addCleanup(self.http.stop)
        self.timer=patch('online_query.threading.Timer');self.timer.start();self.addCleanup(self.timer.stop)
        self.worker=patch('online_query.threading.Thread',side_effect=ImmediateWorker)
        self.worker.start();self.addCleanup(self.worker.stop)

    def response(self,request,timeout):
        context=json.loads(json.loads(request.data)['messages'][1]['content'][0]['text'])
        self.contexts.append(context)
        if self.response_mode=='timeout':raise socket.timeout('synthetic timeout')
        plan=json.loads((ROOT/'prompts/online/plan.example.json').read_text(encoding='utf-8'))
        plan.update(request_id=context['request_id'],snapshot_id=context['snapshot_id'],scene_id=context['scene_id'],
            task_goal=context.get('task_goal') or context['question'])
        plan['observations']=[{'fact':'仅限本帧测试事实','evidence_ids':context['evidence_ids']}]
        plan['missing_information']=['目标标识尚未读清']
        plan['next_step'].update(kind='observe',say='请先确认标识文字。',evidence_ids=context['evidence_ids'])
        if self.response_mode=='unsafe':plan['next_step']['say']='前方没有障碍，可以安全通行'
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())

    def submit(self,question='找厕所',scene='auto',**kwargs):
        return self.query.submit(question,scene,{'captured':time.monotonic(),'jpeg':b'synthetic-fixture',
            'frame_id':'new-frame:'+str(len(self.contexts))},True,**kwargs)

    def test_independent_questions_in_same_scene_never_inherit_old_goal(self):
        self.submit();first=self.query.read()['task']
        self.submit('找出口');second=self.query.read()['task']
        self.assertNotEqual(first['task_id'],second['task_id'])
        self.assertEqual(second['scene'],'find_target');self.assertEqual(second['rounds'],1)
        self.assertEqual(second['goal'],'找出口')
        self.assertNotIn('previous_plan',self.contexts[-1])
        self.assertEqual(self.contexts[-1]['task_goal'],'找出口')

    def test_neutral_followup_preserves_scene_and_only_intent_not_old_visual_facts(self):
        self.submit('我要乘坐2号线');first=self.query.read()['task']
        self.assertTrue(first['can_continue'])
        self.submit('现在呢？',continue_task=True)
        current=self.query.read()['task'];context=self.contexts[-1]
        self.assertEqual(current['task_id'],first['task_id']);self.assertEqual(current['scene'],'metro')
        self.assertEqual(current['rounds'],2);self.assertFalse(current['can_continue'])
        self.assertEqual(context['scene_id'],'metro')
        self.assertEqual(set(context['previous_plan']),{'task_goal','missing_information'})
        self.assertEqual(context['previous_plan']['task_goal'],'我要乘坐2号线')
        self.assertNotIn('observations',context['previous_plan'])
        self.assertEqual(context['frame_id'],'new-frame:1')
        self.assertNotEqual(context['evidence_ids'],self.contexts[0]['evidence_ids'])

    def test_followup_requires_current_task_and_strict_boolean(self):
        with self.assertRaisesRegex(ValueError,'没有可继续'):self.submit(continue_task=True)
        for value in (1,0,'true',None):
            with self.assertRaisesRegex(ValueError,'布尔值'):self.submit(continue_task=value)
        self.assertFalse(self.contexts)

    def test_cross_scene_followup_requires_explicit_new_question(self):
        self.submit('我要乘坐2号线');before=self.query.read()['task']
        with self.assertRaisesRegex(ValueError,'不能切换场景'):
            self.submit('楼梯在哪','stairs',continue_task=True)
        self.assertEqual(self.query.read()['task'],before)
        self.submit('楼梯在哪','stairs')
        self.assertNotEqual(self.query.read()['task']['task_id'],before['task_id'])
        self.assertEqual(self.query.read()['task']['scene'],'stairs')

    def test_expired_task_cannot_continue_but_new_question_can_start(self):
        self.submit();old_id=self.query.task['task_id']
        self.query.task['started']=time.monotonic()-TASK_RESET_AFTER_S-1
        self.assertFalse(self.query.read()['task']['can_continue'])
        self.assertFalse(self.query.read()['task']['has_plan'])
        with self.assertRaisesRegex(ValueError,'任务已过期'):self.submit(continue_task=True)
        self.submit();self.assertNotEqual(self.query.task['task_id'],old_id)
        self.assertEqual(self.query.task['rounds'],1)

    def test_two_round_cap_does_not_block_next_independent_question(self):
        self.submit();self.submit('现在呢？',continue_task=True)
        with self.assertRaisesRegex(ValueError,'两轮'):self.submit('再看一下',continue_task=True)
        self.submit('找出口')
        self.assertEqual(self.query.read()['task']['rounds'],1)

    def test_failed_first_round_is_not_carried_into_followup(self):
        self.response_mode='timeout';self.submit()
        self.assertEqual(self.query.read()['state'],'TIMEOUT')
        self.assertIsNone(self.query.task['last_plan'])
        self.assertTrue(self.query.read()['task']['can_continue'])
        self.response_mode='good';self.submit('请再观察',continue_task=True)
        self.assertNotIn('previous_plan',self.contexts[-1])
        self.assertEqual(self.contexts[-1]['task_goal'],'找厕所')

    def test_semantically_unsafe_candidate_cannot_seed_followup(self):
        self.response_mode='unsafe';self.submit()
        self.assertTrue(self.query.read()['safety_flags'])
        self.assertIsNone(self.query.task['last_plan'])
        self.response_mode='good';self.submit('再确认',continue_task=True)
        self.assertNotIn('previous_plan',self.contexts[-1])

    def test_cancelled_formal_answer_clears_previous_plan(self):
        self.submit();self.assertIsNotNone(self.query.task['last_plan'])
        self.query.cancel();self.assertIsNone(self.query.task['last_plan'])
        self.submit('重新看看',continue_task=True)
        self.assertNotIn('previous_plan',self.contexts[-1])

    def test_followup_deadline_withdraws_previous_plan_and_keeps_task_identity(self):
        self.submit();task_id=self.query.task['task_id']
        with patch('online_query.threading.Thread'):
            request_id=self.submit('请再看看',continue_task=True)
        self.assertTrue(self.query.busy)
        self.query._expire(self.query.generation,request_id)
        self.assertEqual(self.query.read()['state'],'TIMEOUT')
        self.assertEqual(self.query.read()['task_id'],task_id)
        self.assertIsNone(self.query.task['last_plan'])
        self.assertFalse(self.query.read()['task']['has_plan'])

    def test_trial_does_not_change_task_or_use_formal_intent(self):
        self.submit('我要乘坐2号线');before=dict(self.query.task)
        revision='synthetic-review';version=digest(['stairs','测试补充',revision])
        db=self.query.prompts.connect()
        with db:db.execute("INSERT INTO prompt_versions VALUES(?,?,?,?,?,?,datetime('now'))",
            (version,'stairs','测试补充',revision,'source','测试期望'))
        db.close()
        self.submit('楼梯在哪','stairs',prompt_version=version)
        self.assertEqual(self.query.task,before)
        self.assertTrue(self.query.read()['prompt_trial']);self.assertIsNone(self.query.read()['task_id'])
        self.assertNotIn('previous_plan',self.contexts[-1]);self.assertNotIn('task_goal',self.contexts[-1])
        self.query.cancel();self.assertEqual(self.query.task,before)
        with self.assertRaisesRegex(ValueError,'试答不能继续'):
            self.submit('继续','stairs',prompt_version=version,continue_task=True)
        self.submit('现在呢？',continue_task=True)
        self.assertEqual(self.query.task['rounds'],2);self.assertEqual(self.query.task['scene'],'metro')

    def test_camera_change_and_explicit_reset_remove_task(self):
        for reset in (self.query.invalidate_camera,self.query.reset_task):
            self.submit();reset()
            self.assertIsNone(self.query.read()['task']['task_id'])
            self.assertFalse(self.query.read()['task']['can_continue'])
            with self.assertRaises(ValueError):self.submit(continue_task=True)

    def test_task_identity_persists_in_result_json_without_schema_migration(self):
        request_id=self.submit();result=self.query.read();task_id=result['task']['task_id']
        self.assertEqual(result['task_id'],task_id)
        db=self.query.prompts.connect()
        stored=json.loads(db.execute('SELECT result FROM queries WHERE request_id=?',(request_id,)).fetchone()[0])
        columns=[row[1] for row in db.execute('PRAGMA table_info(queries)')];db.close()
        self.assertEqual(stored['task_id'],task_id)
        self.assertEqual(columns,['request_id','created_utc','scene','question','prompt','raw_response','result'])

    def test_late_formal_result_cannot_reseed_a_new_task(self):
        self.submit();old_id=self.query.task['task_id'];generation=self.query.generation
        self.submit('找出口');current=dict(self.query.task)
        self.query.run('synthetic-late-request',generation,'找厕所','find_target',
            {'captured':time.monotonic(),'jpeg':b'synthetic-fixture','frame_id':'old'},task_id=old_id)
        self.assertEqual(self.query.task,current)
        self.assertEqual(self.query.read()['task']['task_id'],current['task_id'])


if __name__=='__main__':unittest.main()
