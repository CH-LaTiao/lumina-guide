"""Consent-bound automatic online help for repeated local uncertainty."""
import time

COOLDOWN_S=30.
STEP_HOLD_S=1.5
BLOCKED_HOLD_S=2.5
MAX_PER_5MIN=3


class AutoHelp:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.enabled=False;self.last_reason=None;self.last_event=None
        self.since=None;self.last_request=float('-inf');self.requests=[];self.last_request_id=None

    def configure(self,enabled,consent):
        if enabled and consent is not True:raise ValueError('自动求助需要先授权自动上传当前画面')
        self.enabled=bool(enabled);self.since=None;self.last_event=None;self.last_reason=None
        if not self.enabled:self.requests.clear()
        return self.read()

    def note_submitted(self,request_id):
        now=self.clock();self.last_request=now;self.last_request_id=request_id
        self.requests.append(now)

    def read(self):
        return {'enabled':self.enabled,'cooldown_s':COOLDOWN_S,
            'trigger':self.last_reason,'last_request_id':self.last_request_id,
            'requests_5min':sum(1 for stamp in self.requests if self.clock()-stamp<=300)}

    def _trigger(self,status):
        guidance=status.get('guidance') or {}
        transition=guidance.get('ground_transition')
        if transition in ('STEP_UP_OR_CURB','DOWN_STEP_OR_DROP'):
            return ('step_uncertainty','stairs',
                '当前画面可能存在台阶或路缘。请只描述可以确认的上行/下行迹象、方向和需要人工确认的事项；不要给安全通行许可。',
                STEP_HOLD_S)
        action=guidance.get('action')
        priority=guidance.get('priority') or 0
        if (action in ('STOP','STOP_AND_CONFIRM','NEAR_OBJECT') and priority>=90
                and guidance.get('candidate') is None):
            return ('blocked_no_route','obstacle',
                '本地检测到持续停车风险且没有已证实通道。请描述可确认的障碍、方向和缺失信息，给出一个观察、停下或求助步骤；不要声称安全可行。',
                BLOCKED_HOLD_S)
        return None

    def evaluate(self,status):
        now=self.clock()
        if not self.enabled:return None
        mode=(status.get('guidance') or {}).get('mode')
        fresh_limit=200 if mode=='WALKING' else 500
        if status.get('state')!='STREAMING' or not 0<=(status.get('frame_age_ms') or 999)<=fresh_limit:
            self.since=None;return None
        online=status.get('online') or {}
        if online.get('busy') or online.get('state') in ('RUNNING','CANDIDATE'):
            self.since=None;return None
        trigger=self._trigger(status)
        if not trigger:self.since=None;return None
        reason,scene,question,hold=trigger
        event=(status.get('session_id'),status.get('guidance',{}).get('event_id'),reason)
        if event!=self.last_event or self.since is None:
            self.last_event=event;self.since=now
        if self.last_request is None:self.last_request=float('-inf')
        if now-self.since<hold or now-self.last_request<COOLDOWN_S:
            return None
        self.requests=[stamp for stamp in self.requests if now-stamp<=300]
        if len(self.requests)>=MAX_PER_5MIN:return None
        self.last_reason=reason
        return {'reason':reason,'scene':scene,'question':question}


def safe_auto_speech(result):
    if result.get('state')!='CANDIDATE' or result.get('gate')!='REVIEW_ONLY':
        return None
    if result.get('safety_flags'):return None
    step=((result.get('plan') or {}).get('next_step') or {})
    if step.get('kind') not in ('explain','ask_user','observe','stop','done','request_assistance'):
        return None
    text=step.get('say')
    if not isinstance(text,str) or not 1<=len(text.strip())<=120:return None
    return text.strip()
