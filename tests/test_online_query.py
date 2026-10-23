import json,os,socket,sys,tempfile,time,unittest,io
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from online_query import OnlineQuery,strict_json,validate,ROOT
class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.plan=json.loads((ROOT/'prompts/online/plan.example.json').read_text())
    def check(self,p):return validate(p,p['request_id'],p['snapshot_id'],'find_target','image:example-1')
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):strict_json('{"a":1,"a":2}')
    def test_unknown_evidence(self):
        self.plan['observations'][0]['evidence_ids']=['made-up']
        with self.assertRaises(ValueError):self.check(self.plan)
    def test_movement_blocked(self):
        self.plan['next_step']['kind']='move_candidate';self.plan['next_step']['requires_local_check']=True
        self.assertEqual(self.check(self.plan),'BLOCKED_MOVEMENT')
    def test_consent_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}):
            q=OnlineQuery(Path(tmp)/'q.sqlite3')
            with self.assertRaises(ValueError):q.submit('test','general',None,False)
            with self.assertRaises(ValueError):q.submit('test','general',{'captured':time.monotonic()-1},True)
    def test_camera_change_cancels_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.result={'state':'CANDIDATE','plan':self.plan};q.generation=4
            q.invalidate_camera();self.assertEqual(q.generation,5);self.assertEqual(q.read()['state'],'CANCELLED')
            self.assertNotIn('plan',q.read())
    def test_mock_response_and_cancel(self):
        def fake(req,timeout):
            context=json.loads(json.loads(req.data)['messages'][1]['content'][0]['text'])
            plan=dict(self.plan,request_id=context['request_id'],snapshot_id=context['snapshot_id'])
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=fake):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1})
            self.assertEqual(q.read()['state'],'CANDIDATE');self.assertFalse(q.read()['spoken'])
            q.result_deadline=0;self.assertEqual(q.read()['state'],'EXPIRED')
            q.cancel();q.run(self.plan['request_id']+'-old',1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':2})
            self.assertEqual(q.read()['state'],'CANCELLED')
    def test_deadline_reports_timeout_not_generic_failure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=socket.timeout('timed out')):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1})
            result=q.read();self.assertEqual(result['state'],'TIMEOUT');self.assertEqual(result['error'],'DEADLINE');self.assertEqual(result['budget_s'],15.0)
    def test_review_mode_uses_separate_budget(self):
        def fake(req,timeout):
            context=json.loads(json.loads(req.data)['messages'][1]['content'][0]['text'])
            plan=dict(self.plan,request_id=context['request_id'],snapshot_id=context['snapshot_id'])
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],evidence_ids=context['evidence_ids'])
            self.assertEqual(timeout,30.0)
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=fake):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1},True,30.0)
            result=q.read();self.assertEqual(result['state'],'CANDIDATE');self.assertTrue(result['review']);self.assertEqual(result['budget_s'],30.0)
    def test_miscopied_ids_are_restamped(self):
        def fake(req,timeout):
            context=json.loads(json.loads(req.data)['messages'][1]['content'][0]['text'])
            plan=dict(self.plan,request_id='00000000-0000-4000-8000-000000000000',snapshot_id='11111111-1111-4111-8111-111111111111')
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=fake):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1})
            result=q.read();self.assertEqual(result['state'],'CANDIDATE');self.assertTrue(result['ids_normalized']);self.assertEqual(result['plan']['request_id'],self.plan['request_id'])
    def test_task_rounds_capped_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}):
            q=OnlineQuery(Path(tmp)/'q.sqlite3')
            self.assertEqual(q._begin_round('find_target')[0],1)
            self.assertEqual(q._begin_round('find_target')[0],2)
            with self.assertRaises(ValueError):q._begin_round('find_target')
            q.reset_task()
            self.assertEqual(q._begin_round('find_target')[0],1)
    def test_second_round_carries_previous_plan(self):
        seen={}
        def fake(req,timeout):
            context=json.loads(json.loads(req.data)['messages'][1]['content'][0]['text'])
            seen.update(context)
            plan=dict(self.plan,request_id=context['request_id'],snapshot_id=context['snapshot_id'])
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}):
            with patch('online_query.fetch_response',side_effect=fake):
                q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
                q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1},
                    round_no=2,previous={'task_goal':'找到厕所','next_step':{'kind':'observe'},'missing_information':['入口位置']})
        self.assertEqual(seen['task_round'],2)
        self.assertEqual(seen['previous_plan']['task_goal'],'找到厕所')
    def test_unsafe_prose_is_flagged(self):
        def fake(req,timeout):
            context=json.loads(json.loads(req.data)['messages'][1]['content'][0]['text'])
            plan=dict(self.plan,request_id=context['request_id'],snapshot_id=context['snapshot_id'])
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],say='前方没有障碍，可以安全通行',evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=fake):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1})
            result=q.read();self.assertEqual(result['state'],'CANDIDATE')
            self.assertTrue(result['safety_flags']);self.assertIn('语义审核提示',result['note'])
    def test_request_uses_fast_model_without_thinking(self):
        seen={}
        def fake(req,timeout):
            body=json.loads(req.data)
            seen['model']=body.get('model');seen['thinking']=body.get('enable_thinking')
            context=json.loads(body['messages'][1]['content'][0]['text'])
            plan=dict(self.plan,request_id=context['request_id'],snapshot_id=context['snapshot_id'])
            plan['observations']=[{'fact':'mock','evidence_ids':context['evidence_ids']}]
            plan['next_step']=dict(plan['next_step'],evidence_ids=context['evidence_ids'])
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(plan)}}]}).encode())
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'test-only'}),patch('online_query.fetch_response',side_effect=fake):
            q=OnlineQuery(Path(tmp)/'q.sqlite3');q.generation=1
            q.run(self.plan['request_id'],1,'test','find_target',{'captured':time.monotonic(),'jpeg':b'test','frame_id':1})
        self.assertEqual(seen['model'],'qwen3.8-flash')
        self.assertIs(seen['thinking'],False)
if __name__=='__main__':unittest.main()
