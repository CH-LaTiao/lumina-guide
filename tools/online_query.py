"""On-demand cloud plan candidate; never actuates or speaks model text."""
import base64, hashlib, json, os, socket, sqlite3, threading, time, urllib.request, urllib.error, uuid
import io
import math
from http_transport import request as isolated_request
from scene_router import scene_for_question
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from safety_review import review as review_plan
from runtime_paths import runtime_dir
from prompt_registry import PromptRegistry
from cloud_config import api_host

ROOT=Path(__file__).resolve().parents[1]
HOST=api_host()
SCENES={'general','find_target','obstacle','metro','stairs','traffic_signal','read_sign'}
# Action requests use a bounded interactive budget; review mode stays explicit:
# longer waits are only for human inspection and never re-enable movement output.
ACTION_BUDGET_S=15.0
REVIEW_BUDGET_S=30.0
TASK_MAX_ROUNDS=2
TASK_RESET_AFTER_S=180
# The online budget is independent of local perception and guidance.
MODEL=os.environ.get('ONLINE_MODEL','qwen3.8-flash')
ENABLE_THINKING=False

def fetch_response(req, timeout):
    return io.BytesIO(isolated_request(req,timeout,getattr(req,'cancel_event',None)))

def strict_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def constant(_):raise ValueError('Non-finite JSON')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)

def validate(plan,request_id,snapshot_id,scene,evidence):
    schema=json.loads((ROOT/'prompts/online/plan.schema.json').read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(plan)
    if (plan['request_id'],plan['snapshot_id'],plan['scene_id'])!=(request_id,snapshot_id,scene):raise ValueError('Mismatched task or snapshot')
    for refs in [o['evidence_ids'] for o in plan['observations']]+[plan['next_step']['evidence_ids']]:
        if any(ref!=evidence for ref in refs):raise ValueError('Unknown evidence')
    # No movement gate is implemented yet. Candidate prose is UI-only, never TTS.
    return 'BLOCKED_MOVEMENT' if plan['next_step']['kind']=='move_candidate' else 'REVIEW_ONLY'

class OnlineQuery:
    def __init__(self, db_path=None):
        self.lock=threading.Lock();self.generation=0;self.busy=False;self.result_deadline=0
        self.cancel_event=threading.Event();self.timer=None;self.workers=0
        self.task=self._empty_task()
        self.result={'state':'IDLE','configured':bool(os.environ.get('DASHSCOPE_API_KEY'))}
        self.db_path=Path(db_path) if db_path else runtime_dir()/'queries.sqlite3'
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self.prompts=PromptRegistry(self.db_path)
        db=sqlite3.connect(self.db_path)
        try:
            with db:db.execute('CREATE TABLE IF NOT EXISTS queries(request_id TEXT PRIMARY KEY,created_utc TEXT,scene TEXT,question TEXT,prompt TEXT,raw_response TEXT,result TEXT)')
        finally:db.close()
    def read(self):
        with self.lock:
            now=time.monotonic()
            active=bool(self.task['task_id']) and 0<=now-self.task['started']<TASK_RESET_AFTER_S
            result=dict(self.result,busy=self.busy,configured=bool(os.environ.get('DASHSCOPE_API_KEY')),
                task={'task_id':self.task['task_id'],'scene':self.task['scene'],
                    'rounds':self.task['rounds'],'max_rounds':TASK_MAX_ROUNDS,
                    'has_plan':active and self.task['last_plan'] is not None,
                    'can_continue':active and self.task['rounds']<TASK_MAX_ROUNDS and not self.busy,
                    'goal':self.task['goal'] if active else ''})
            if result.get('state')=='CANDIDATE' and time.monotonic()>self.result_deadline:
                result['state']='EXPIRED';result['note']='历史结果已过期，仅供审阅，请重新观察。'
            return result
    def cancel(self):
        with self.lock:
            self._cancel_locked()
    def _cancel_locked(self):
        if not self.result.get('prompt_trial'):self.task['last_plan']=None
        previous=self.result
        self.cancel_event.set();self.generation+=1;self.busy=False;self.result={
            'state':'CANCELLED','prompt_trial':previous.get('prompt_trial',False),'task_id':previous.get('task_id')}
        if self.timer:self.timer.cancel()
    def _expire(self,generation,rid):
        with self.lock:
            if generation==self.generation and self.busy:
                if not self.result.get('prompt_trial'):self.task['last_plan']=None
                previous=self.result
                self.cancel_event.set();self.generation+=1;self.busy=False
                self.result={'state':'TIMEOUT','request_id':rid,'error':'DEADLINE','note':'任务已到期，请重新观察。',
                    'prompt_trial':previous.get('prompt_trial',False),'task_id':previous.get('task_id')}
    def invalidate_camera(self):
        with self.lock:
            self.task=self._empty_task()
            if self.result.get('state') in ('RUNNING','CANDIDATE'):
                self.cancel_event.set();self.busy=False
                if self.timer:self.timer.cancel()
                self.generation+=1;self.result={'state':'CANCELLED','note':'相机连接已变化，请重新观察后提问。'}
    def reset_task(self):
        with self.lock:
            self._cancel_locked();self.task=self._empty_task()
    @staticmethod
    def _empty_task():
        return {'task_id':None,'rounds':0,'started':0.0,'scene':None,'last_plan':None,'goal':''}
    def _begin_round(self,scene,new_task=False,question=''):
        now=time.monotonic()
        if new_task or self.task['scene']!=scene or not self.task['task_id']:
            self.task=dict(self._empty_task(),task_id=str(uuid.uuid4()),started=now,scene=scene,goal=question)
        if self.task['rounds']>=TASK_MAX_ROUNDS and now-self.task['started']<TASK_RESET_AFTER_S:
            raise ValueError('当前任务已完成两轮，请稍后再问或开始新任务')
        if self.task['rounds']==0 or now-self.task['started']>=TASK_RESET_AFTER_S:
            self.task=dict(self._empty_task(),task_id=str(uuid.uuid4()),started=now,scene=scene,goal=question)
        self.task['rounds']+=1
        return self.task['rounds'],self.task['last_plan']
    def submit(self,question,scene,snapshot,consent,review=False,prompt_version=None,continue_task=False):
        if consent is not True:raise ValueError('需要先勾选本次上传授权')
        if type(continue_task) is not bool:raise ValueError('继续任务选项必须为布尔值')
        if scene not in SCENES|{'auto'} or not isinstance(question,str) or not 1<=len(question.strip())<=300:raise ValueError('场景或问题无效')
        if not os.environ.get('DASHSCOPE_API_KEY'):raise ValueError('未配置在线密钥')
        age=time.monotonic()-snapshot['captured'] if snapshot is not None else math.inf
        if not math.isfinite(age) or not 0<=age<=.5:raise ValueError('没有新鲜的相机画面，请稍后再试')
        trial=prompt_version is not None
        if trial and continue_task:raise ValueError('提示词试答不能继续正式任务')
        review=review is True or trial;budget=REVIEW_BUDGET_S if review else ACTION_BUDGET_S
        with self.lock:
            if self.busy or self.workers>=2:raise ValueError('在线任务进行中或旧任务正在回收')
            if continue_task:
                age=time.monotonic()-self.task['started']
                if not self.task['task_id'] or not 0<=age<TASK_RESET_AFTER_S:
                    raise ValueError('没有可继续的当前任务或任务已过期，请发送新问题')
                if scene=='auto':scene=self.task['scene']
                if scene!=self.task['scene']:raise ValueError('继续任务不能切换场景，请发送新问题')
            elif scene=='auto':scene=scene_for_question(question)
            bundle=self.prompts.bundle(scene,version=prompt_version,trial=trial)
            round_no,previous=(1,None) if trial else self._begin_round(scene,new_task=not continue_task,question=question.strip())
            task_id=None if trial else self.task['task_id']
            task_goal=None if trial else self.task['goal']
            self.busy=True;self.generation+=1;generation=self.generation
            self.cancel_event=threading.Event();cancel_event=self.cancel_event;self.workers+=1
            rid=str(uuid.uuid4());self.result={'state':'RUNNING','request_id':rid,'review':review,'budget_s':budget,'task_round':round_no,
                'prompt_trial':trial,'prompt_version':bundle['version'],'prompt_sha256':bundle['sha256'],
                'task_id':task_id,'scene':scene}
            self.timer=threading.Timer(budget,self._expire,args=(generation,rid));self.timer.daemon=True;self.timer.start()
        threading.Thread(target=self.run,args=(rid,generation,question,scene,snapshot),
            kwargs={'review':review,'budget':budget,'round_no':round_no,'previous':previous,'cancel_event':cancel_event,'counted':True,
                    'prompt_bundle':bundle,'trial':trial,'task_id':task_id,'task_goal':task_goal},daemon=True).start()
        return rid
    def run(self,rid,generation,question,scene,snapshot,review=False,budget=ACTION_BUDGET_S,round_no=1,previous=None,cancel_event=None,counted=False,prompt_bundle=None,trial=False,task_id=None,task_goal=None):
        prompt='';raw='';started=time.monotonic();sid=str(uuid.uuid4());evidence='image:'+sid
        result={'state':'FAILED','request_id':rid,'review':review,'budget_s':budget}
        provenance={'prompt_trial':trial,'task_id':task_id}
        try:
            bundle=prompt_bundle or self.prompts.bundle(scene)
            prompt=bundle['prompt']
            provenance.update(prompt_version=bundle['version'],prompt_sha256=bundle['sha256'],
                image_sha256=hashlib.sha256(snapshot['jpeg']).hexdigest(),model=MODEL,
                prompt_fallback=bundle.get('fallback_reason'))
            context={'request_id':rid,'snapshot_id':sid,'scene_id':scene,'template_version':bundle['version'],
                     'template_sha256':bundle['sha256'],
                     'question':question,'evidence_ids':[evidence],'frame_id':snapshot['frame_id'],
                     'body_pose':'unknown','local_movement_gate':'not_implemented','map':'unknown'}
            context['task_round']=round_no
            context['task_id']=task_id
            if task_goal:context['task_goal']=task_goal
            context['interaction_mode']='STANDING_USER_SELECTED_NOT_PHYSICALLY_VERIFIED'
            if trial:context['purpose']='PROMPT_TRIAL_FOR_HUMAN_REVIEW_ONLY'
            if previous:
                context['previous_plan']={'task_goal':previous.get('task_goal'),
                    'missing_information':previous.get('missing_information')}
                context['history_scope']='Only intent persists. Re-observe all visual facts in the current image.'
            body={'model':MODEL,'messages':[{'role':'system','content':prompt},{'role':'user','content':[
                {'type':'text','text':json.dumps(context,ensure_ascii=False)},
                {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(snapshot['jpeg']).decode()}}]}],
                'max_tokens':1200,'temperature':0,'response_format':{'type':'json_object'},
                'enable_thinking':ENABLE_THINKING}
            req=urllib.request.Request(HOST+'/compatible-mode/v1/chat/completions',data=json.dumps(body).encode(),headers={
                'Authorization':'Bearer '+os.environ['DASHSCOPE_API_KEY'],'Content-Type':'application/json'})
            req.cancel_event=cancel_event
            with fetch_response(req,timeout=budget) as response:
                payload=response.read(65537)
            if len(payload)>65536:raise ValueError('Response exceeds size limit')
            raw=strict_json(payload.decode())['choices'][0]['message']['content']
            plan=strict_json(raw)
            # Task identity is bookkeeping owned by this client, not a model claim:
            # re-stamp it so a mis-copied UUID cannot invalidate an otherwise valid plan.
            ids_ok=isinstance(plan,dict) and (plan.get('request_id'),plan.get('snapshot_id'),plan.get('scene_id'))==(rid,sid,scene)
            if isinstance(plan,dict):plan['request_id']=rid;plan['snapshot_id']=sid;plan['scene_id']=scene
            decision=validate(plan,rid,sid,scene,evidence)
            expired=time.monotonic()-snapshot['captured']>budget or time.monotonic()-started>budget
            flags=review_plan(plan)
            note='模型候选，仅供调试审阅，不是已经核验的行走指令。'
            if flags:note+=f'语义审核提示{len(flags)}项，需人工复核。'
            result={'state':'EXPIRED' if expired else 'CANDIDATE','request_id':rid,'frame_id':snapshot['frame_id'],
                    'scene':scene,'plan':plan,'gate':decision,'elapsed_ms':(time.monotonic()-started)*1000,
                    'review':review,'budget_s':budget,'model':MODEL,'ids_normalized':not ids_ok,'task_round':round_no,
                    'safety_flags':flags,'spoken':False,'real_output':False,'note':note}
        except urllib.error.HTTPError as exc:result.update(error='HTTP '+str(exc.code))
        except urllib.error.URLError as exc:
            if isinstance(exc.reason,(TimeoutError,socket.timeout)):
                result.update(state='TIMEOUT',error='DEADLINE',note=f'超出{budget:g}秒预算，未取得可用结果。'+('' if review else '可勾选审阅模式重试。'))
            else:result.update(state='NETWORK_ERROR',error=type(exc.reason).__name__)
        except (TimeoutError,socket.timeout):
            result.update(state='TIMEOUT',error='DEADLINE',note=f'超出{budget:g}秒预算，未取得可用结果。'+('' if review else '可勾选审阅模式重试。'))
        except Exception as exc:result.update(error=type(exc).__name__)
        result.update(provenance)
        with self.lock:
            if generation!=self.generation:result={'state':'CANCELLED','request_id':rid,'late_result_discarded':True,**provenance}
        try:
            db=sqlite3.connect(self.db_path,timeout=.2)
            try:
                with db:db.execute("INSERT INTO queries VALUES(?,datetime('now'),?,?,?,?,?)",(rid,scene,question,prompt,raw,json.dumps(result,ensure_ascii=False)))
            finally:db.close()
        except sqlite3.Error:result['log_error']='DATABASE_WRITE_FAILED'
        with self.lock:
            if counted:self.workers=max(0,self.workers-1)
            if generation==self.generation:
                self.busy=False
                if self.timer:self.timer.cancel()
                self.result=result;self.result_deadline=snapshot['captured']+budget
                if not trial and task_id is not None and self.task['task_id']==task_id:
                    if result.get('state')=='CANDIDATE' and not result.get('safety_flags'):
                        plan=result['plan']
                        self.task['last_plan']={'task_goal':plan.get('task_goal'),
                            'missing_information':plan.get('missing_information')}
                        self.task['goal']=plan.get('task_goal') or self.task['goal']
                    else:self.task['last_plan']=None
