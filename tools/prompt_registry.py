"""Version scene supplements, assess real trial answers, and publish with an audit trail.

This is prompt configuration, not model-weight training. Base rules and output schema
remain application-owned. CSV import never publishes a version.
"""
import csv
import hashlib
import io
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from review_store import ReviewStore, SCENES, digest, safe_cell
from safety_review import review as safety_review

PROMPTS = Path(__file__).resolve().parents[1]/'prompts/online'


class PromptRegistry(ReviewStore):
    def __init__(self, db_path=None, prompts=PROMPTS):
        super().__init__(db_path)
        self.prompts = Path(prompts)
        db = self.connect()
        try:
            with db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS prompt_versions(
                      version TEXT PRIMARY KEY,scene TEXT NOT NULL,supplement TEXT NOT NULL,
                      revision TEXT NOT NULL UNIQUE,request_id TEXT NOT NULL,
                      expected_answer TEXT NOT NULL,created_utc TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS prompt_active(
                      scene TEXT PRIMARY KEY,version TEXT,generation INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS prompt_assessments(
                      id INTEGER PRIMARY KEY,request_id TEXT NOT NULL,version TEXT NOT NULL,
                      prompt_sha256 TEXT NOT NULL,accepted INTEGER NOT NULL,checks TEXT NOT NULL,
                      note TEXT NOT NULL,created_utc TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS prompt_events(
                      id INTEGER PRIMARY KEY,scene TEXT NOT NULL,before_version TEXT,after_version TEXT,
                      generation INTEGER NOT NULL,kind TEXT NOT NULL,note TEXT NOT NULL,created_utc TEXT NOT NULL);
                ''')
        finally:
            db.close()

    def sync_candidates(self):
        candidates = self.candidates()['items']
        db = self.connect(); added = 0
        try:
            with db:
                for item in candidates:
                    text = item['prompt_candidate'].strip()
                    if not text or item['scene'] not in SCENES:continue
                    version = digest([item['scene'], text, item['revision']])
                    cursor = db.execute("INSERT OR IGNORE INTO prompt_versions VALUES(?,?,?,?,?,?,datetime('now'))",
                        (version,item['scene'],text,item['revision'],item['request_id'],item['expected_answer']))
                    added += cursor.rowcount
        finally:
            db.close()
        return {'created':added,'status':'DRAFT_ONLY'}

    def _version(self, db, version):
        row = db.execute('SELECT * FROM prompt_versions WHERE version=?',(version,)).fetchone()
        if row is None:raise ValueError('提示词版本不存在')
        if digest([row['scene'],row['supplement'],row['revision']]) != row['version']:
            raise ValueError('提示词版本校验失败')
        return dict(row)

    def version(self, version):
        db = self.connect()
        try:return self._version(db,version)
        finally:db.close()

    def _active(self, db, scene):
        row = db.execute('SELECT * FROM prompt_active WHERE scene=?',(scene,)).fetchone()
        return dict(row) if row else {'scene':scene,'version':None,'generation':0}

    def _bundle(self, db, scene, version):
        if scene not in SCENES:raise ValueError('场景无效')
        supplement = ''
        if version:
            row = self._version(db,version)
            if row['scene'] != scene:raise ValueError('候选版本与请求场景不一致')
            supplement = '\n人工修订的场景补充（沿用共用规则与Schema）：\n' + row['supplement']
        schema = json.loads((self.prompts/'plan.schema.json').read_text(encoding='utf-8'))
        prompt = ((self.prompts/'base.txt').read_text(encoding='utf-8')+'\n'
                  +(self.prompts/(scene+'.txt')).read_text(encoding='utf-8')+supplement
                  +'\nJSON Schema:\n'+json.dumps(schema,ensure_ascii=False))
        return {'version':version or 'builtin:'+scene,'scene':scene,'prompt':prompt,
                'sha256':hashlib.sha256(prompt.encode()).hexdigest(),'schema':schema}

    def bundle(self, scene, version=None, trial=False):
        db = self.connect()
        try:
            selected = version if trial else self._active(db,scene)['version']
            if trial and not selected:raise ValueError('请选择候选提示词版本')
            fallback=None
            if selected and not trial and not self._eligible(db,scene,selected):
                selected=None;fallback='ACTIVE_PROMPT_NEEDS_REVIEW'
            result=self._bundle(db,scene,selected)
            result['fallback_reason']=fallback
            return result
        finally:db.close()

    def _trial(self, db, request_id):
        source = db.execute('SELECT * FROM queries WHERE request_id=?',(request_id,)).fetchone()
        if source is None:raise ValueError('试答记录尚未保存或不存在')
        result = json.loads(source['result'])
        version = result.get('prompt_version')
        if result.get('prompt_trial') is not True or not isinstance(version,str):
            raise ValueError('只能评测使用候选版本生成的试答')
        row = self._version(db,version)
        bundle = self._bundle(db,row['scene'],version)
        plan = result.get('plan') or {}
        schema_valid = not list(Draft202012Validator(bundle['schema']).iter_errors(plan))
        checks = {'completed':result.get('state')=='CANDIDATE',
                  'schema':schema_valid,'same_prompt':result.get('prompt_sha256')==bundle['sha256']
                      and hashlib.sha256(source['prompt'].encode()).hexdigest()==bundle['sha256'],
                  'same_scene':source['scene']==row['scene']==result.get('scene'),
                  'no_safety_flags':not result.get('safety_flags') and not safety_review(plan),
                  'no_real_output':result.get('real_output') is False and result.get('spoken') is False}
        return dict(source), result, bundle, checks

    def assess_trial(self, request_id, accepted, checks, note):
        if not isinstance(accepted,bool):raise ValueError('请选择通过或不通过')
        if not isinstance(note,str) or not 1<=len(note.strip())<=2000:raise ValueError('请填写1—2000字的评测说明')
        required = ('scene_correct','answers_question','next_step_useful')
        if not isinstance(checks,dict) or any(not isinstance(checks.get(k),bool) for k in required):
            raise ValueError('请逐项填写场景、回答和下一步建议的核对结果')
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                source,result,bundle,automatic = self._trial(db,request_id)
                if accepted and (not all(automatic.values()) or not all(checks[k] for k in required)):
                    raise ValueError('结构、时效、规则检查或人工核对未通过，不能标为通过')
                all_checks = {'automatic':automatic,'human':{k:checks[k] for k in required}}
                encoded = json.dumps(all_checks,ensure_ascii=False,sort_keys=True)
                last = db.execute('SELECT * FROM prompt_assessments WHERE request_id=? ORDER BY id DESC LIMIT 1',(request_id,)).fetchone()
                if last and (last['accepted'],last['checks'],last['note'])==(int(accepted),encoded,note.strip()):
                    return {'assessment_id':last['id'],'accepted':accepted,'skipped':True}
                cur = db.execute("INSERT INTO prompt_assessments(request_id,version,prompt_sha256,accepted,checks,note,created_utc) VALUES(?,?,?,?,?,?,datetime('now'))",
                    (request_id,bundle['version'],bundle['sha256'],int(accepted),encoded,note.strip()))
                return {'assessment_id':cur.lastrowid,'accepted':accepted,'skipped':False}
        finally:db.close()

    def _eligible(self, db, scene, version):
        if version is None:return True
        bundle = self._bundle(db,scene,version)
        rows = db.execute('''SELECT * FROM prompt_assessments WHERE version=? AND accepted=1
            AND id IN (SELECT max(id) FROM prompt_assessments GROUP BY request_id)''',(version,))
        for row in rows:
            if row['prompt_sha256'] != bundle['sha256']:continue
            _,_,_,checks = self._trial(db,row['request_id'])
            if all(checks.values()):return True
        return False

    def activate(self, scene, version, generation, note, rollback=False):
        if scene not in SCENES or type(generation) is not int:raise ValueError('场景或版本序号无效')
        if not isinstance(note,str) or not 1<=len(note.strip())<=2000:raise ValueError('请填写变更说明')
        db = self.connect()
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                active = self._active(db,scene)
                if active['generation'] != generation:raise ValueError('当前版本已变化，请刷新后再操作')
                if rollback:
                    event = db.execute('SELECT * FROM prompt_events WHERE scene=? ORDER BY id DESC LIMIT 1',(scene,)).fetchone()
                    if event is None:raise ValueError('此场景还没有可回滚的变更')
                    version = event['before_version']
                if not self._eligible(db,scene,version):raise ValueError('该版本尚无通过的试答评测，或共用模板已变化，请重新试答')
                if active['version'] == version:return dict(active,skipped=True)
                generation += 1
                db.execute('INSERT OR REPLACE INTO prompt_active VALUES(?,?,?)',(scene,version,generation))
                db.execute("INSERT INTO prompt_events(scene,before_version,after_version,generation,kind,note,created_utc) VALUES(?,?,?,?,?,?,datetime('now'))",
                    (scene,active['version'],version,generation,'ROLLBACK' if rollback else 'ACTIVATE',note.strip()))
                return {'scene':scene,'version':version,'generation':generation,'skipped':False}
        finally:db.close()

    def status(self):
        db = self.connect()
        try:
            versions = []
            for row in db.execute('SELECT * FROM prompt_versions ORDER BY created_utc DESC,rowid DESC LIMIT 200'):
                item = self._version(db,row['version'])
                original = db.execute('SELECT question FROM queries WHERE request_id=?',(row['request_id'],)).fetchone()
                item.update(question=original['question'] if original else '',eligible=self._eligible(db,row['scene'],row['version']))
                versions.append(item)
            trials = []
            for row in db.execute("SELECT * FROM queries WHERE json_valid(result) AND json_extract(result,'$.prompt_trial')=1 ORDER BY created_utc DESC,rowid DESC LIMIT 50"):
                result = json.loads(row['result'])
                assessment = db.execute('SELECT * FROM prompt_assessments WHERE request_id=? ORDER BY id DESC LIMIT 1',(row['request_id'],)).fetchone()
                trials.append({'request_id':row['request_id'],'created_utc':row['created_utc'],'scene':row['scene'],
                    'question':row['question'],'result':result,'assessment':dict(assessment) if assessment else None})
            active=[]
            for scene in sorted(SCENES):
                value=self._active(db,scene)
                value['needs_review']=bool(value['version']) and not self._eligible(db,scene,value['version'])
                active.append(value)
            return {'active':active,'versions':versions,'trials':trials,
                'events':[dict(r) for r in db.execute('SELECT * FROM prompt_events ORDER BY id DESC LIMIT 50')],
                'training':'PROMPT_CONFIGURATION_ONLY'}
        finally:db.close()

    def export_report(self):
        fields=['request_id','created_utc','scene','question','prompt_version','prompt_sha256','actual_prompt',
                'expected_answer','result_state','model','elapsed_ms','answer','latest_assessment','review_note']
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fields);writer.writeheader()
        db=self.connect()
        try:
            for row in db.execute("SELECT * FROM queries WHERE json_valid(result) AND json_extract(result,'$.prompt_trial')=1 ORDER BY created_utc,rowid"):
                result=json.loads(row['result']);version=self._version(db,result['prompt_version'])
                assessment=db.execute('SELECT * FROM prompt_assessments WHERE request_id=? ORDER BY id DESC LIMIT 1',(row['request_id'],)).fetchone()
                value={'request_id':row['request_id'],'created_utc':row['created_utc'],'scene':row['scene'],'question':row['question'],
                    'prompt_version':version['version'],'prompt_sha256':result.get('prompt_sha256'),'actual_prompt':row['prompt'],
                    'expected_answer':version['expected_answer'],'result_state':result.get('state'),'model':result.get('model'),
                    'elapsed_ms':result.get('elapsed_ms'),'answer':json.dumps(result.get('plan'),ensure_ascii=False),
                    'latest_assessment':('PASS' if assessment['accepted'] else 'FAIL') if assessment else 'UNREVIEWED',
                    'review_note':assessment['note'] if assessment else ''}
                writer.writerow({k:safe_cell(value.get(k)) for k in fields})
        finally:db.close()
        return ('\ufeff'+stream.getvalue()).encode('utf-8')
