"""Render the current local guidance event; never choose a route or write hardware."""
import json
import sqlite3
import threading
import time
from collections import deque
from runtime_paths import runtime_dir

CURRENT_TTL_S=.6

def _channels(regions):
    return [2*index+offset for index in sorted(set(regions)) for offset in (0,1)]

class FeedbackSimulator:
    def __init__(self,db_path=None,clock=time.monotonic):
        self.clock=clock;self.lock=threading.RLock();self.paused=False;self.current=None
        self.guidance_event_key=None;self.last_mapping='camera_regions'
        self.events=deque(maxlen=30);self.log_error=None
        self.db_path=db_path or runtime_dir()/'feedback.sqlite3'
        db=sqlite3.connect(self.db_path)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS feedback_events(id INTEGER PRIMARY KEY, created_utc TEXT NOT NULL, event TEXT NOT NULL)')
            db.commit()
        finally:db.close()

    def _event(self,value):
        event=dict(value,simulated=True,real_output=False)
        try:
            db=sqlite3.connect(self.db_path,timeout=.2)
            try:
                with db:
                    cursor=db.execute("INSERT INTO feedback_events(created_utc,event) VALUES(datetime('now'),?)",
                        (json.dumps(event,ensure_ascii=False),))
                    event['event_id']=cursor.lastrowid
            finally:db.close()
            self.log_error=None
        except sqlite3.Error:self.log_error='DATABASE_WRITE_FAILED'
        self.events.appendleft(event)

    def pause(self,paused):
        if type(paused) is not bool:raise ValueError('paused must be boolean')
        with self.lock:
            self.paused=paused;self.current=None;self.guidance_event_key=None
            self._event({'kind':'PAUSED' if paused else 'RESUMED',
                'text':'模拟已暂停' if paused else '模拟已恢复，等待新鲜感知'})

    def update(self,info,guidance_only=True):
        with self.lock:
            if self.paused:return
            guidance=info.get('guidance')
            if not isinstance(guidance,dict) or guidance.get('mode')=='PAUSED':
                self.current=None;return
            mode=guidance.get('mode');action=guidance.get('action') or 'OBSERVE'
            speech=guidance.get('speech')
            fresh=guidance.get('fresh') is True and info.get('state')=='STREAMING'
            priority=guidance.get('priority',0)
            if not fresh:
                action='STOP' if mode=='WALKING' else 'UNAVAILABLE'
                speech='停，感知不可用。' if mode=='WALKING' else '感知不可用。'
                priority=90
            if not isinstance(speech,str) or not speech:
                self.current=None;return
            body=guidance.get('direction_frame')=='BODY'
            candidate=guidance.get('candidate');channels=[];direction=None
            mapping='body_regions' if body else 'camera_regions'
            if (fresh and body and action in ('DIRECTION_CANDIDATE','STOP_AND_DETOUR')
                    and candidate in ('LEFT','CENTER','RIGHT') and guidance.get('can_continue') is True):
                direction=candidate;mapping='body_direction'
                channels=_channels([{'LEFT':0,'CENTER':1,'RIGHT':2}[candidate]])
            elif fresh and action in ('STOP','STOP_AND_CONFIRM','STOP_AND_DETOUR','NEAR_OBJECT','UNKNOWN'):
                # Regions belong to the same accepted event as the speech.
                regions=guidance.get('hazard_regions') or []
                if not body or guidance.get('native_body_ambiguous') is False:
                    channels=_channels([i for i in regions if type(i) is int and 0<=i<=2])
                    if not channels and 'GROUND_GAP' in (guidance.get('reasons') or []):channels=_channels([1])
                if direction is None:direction=guidance.get('direction_hint')
            event_id=guidance.get('event_id')
            key=(mode,action,speech,tuple(channels),direction)
            current={'kind':'GUIDANCE:'+action,'text':speech,'priority':priority,
                'guidance_event_id':event_id,'frame_id':guidance.get('frame_id'),
                'action':action,'direction':direction,'channels':channels,'mapping':mapping,
                'fresh':fresh,'pulse_ms':150 if channels else 0,'deadline':self.clock()+CURRENT_TTL_S}
            self.current=current;self.last_mapping=mapping
            if key!=self.guidance_event_key:
                self._event({k:v for k,v in current.items() if k!='deadline'})
                self.guidance_event_key=key

    def read(self):
        with self.lock:
            active=self.current if self.current and self.current['deadline']>=self.clock() else None
            return {'mode':'SIMULATION','paused':self.paused,'real_output':False,
                'speech':{k:v for k,v in active.items() if k!='deadline'} if active else None,
                'haptics':[{'channel':i,'active':bool(active and i in active['channels'])} for i in range(6)],
                'mapping':active['mapping'] if active else self.last_mapping,
                'events':list(self.events),'log_error':self.log_error}
