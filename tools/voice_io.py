"""Explicit short recording, cloud ASR and preemptible local Mandarin TTS.

No background recording, camera upload or arbitrary shell commands. NoMachine
source/sink routing is checked independently of semantic/model availability.
"""
import base64
import io
import json
import os
import select
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path
import numpy as np
from http_transport import request

SOURCE='nx_remapped_out'
SINK='alsa_output.platform-sound.analog-stereo'
PIPER_MODELS=(
    os.environ.get('LUMINA_PIPER_MODEL'),
    str(Path.home()/'lumina-models/piper/zh_CN-huayan-medium.onnx'),
    str(Path.home()/'yahboom_ws/src/largemodel/MODELS/tts/zh/zh_CN-huayan-medium.onnx'))


def piper_model_path():
    return next((path for path in PIPER_MODELS if path and Path(path).is_file()),None)
WALKING_DIRECTIONS=('左前方','左侧','稍左','前方','稍右','右侧','右前方')
PREWARM_PHRASES=(
    '停，感知不可用。','停，深度异常。','停，深度不足。',
    '停，有台阶。','停，有落差。','停，通道过窄。',
    '停，前方有障碍。','停，方向未知。','正在确认路线。',
    '停，前方有物。','停，左侧有物。','停，右侧有物。',
    '停，左侧有障碍。','停，右侧有障碍。',
    '前方近物，往左。','前方近物，往右。','三面近物，停。',
    '左侧近物，走中。','右侧近物，走中。','两侧近物，走中。',
    '右侧近物，偏左。','左侧近物，偏右。','前方近物，偏左。','前方近物，偏右。','前方近物，停。',
    '前方近物，慢行。','近处有物。','近处近物，慢行。',
    '停，前方近物。','停，左侧近物。','停，右侧近物。','停，两侧近物。')+tuple(
        phrase for direction in WALKING_DIRECTIONS for phrase in (
            direction+'可走。',direction+'可走，窄。','能走，'+direction+'变窄。',
            '停，改走'+direction+'。','停，改走'+direction+'窄。',
            '停，台阶，改走'+direction+'。','停，落差，改走'+direction+'。'))+tuple(
        f'停，{side}可能{name}。' for side in ('左侧','前方','右侧')
        for name in ('行人','椅子','汽车'))
CORE_PREWARM_PHRASES=(
    '停，感知不可用。','停，深度异常。','停，深度不足。',
    '停，有台阶。','停，有落差。','停，通道过窄。',
    '停，前方有障碍。','停，方向未知。','正在确认路线。',
    '前方可走。','左侧可走。','右侧可走。',
    '停，改走前方。','停，改走左侧。','停，改走右侧。',
    '前方近物，往左。','前方近物，往右。',
    '左侧近物，走中。','右侧近物，走中。','两侧近物，走中。',
    '右侧近物，偏左。','左侧近物，偏右。','前方近物，停。')
EXTRA_PREWARM_PHRASES=tuple(text for text in PREWARM_PHRASES
    if text not in CORE_PREWARM_PHRASES)


def devices():
    env=dict(os.environ,PULSE_SERVER='unix:/run/user/1000/pulse/native',XDG_RUNTIME_DIR='/run/user/1000')
    try:
        def read(kind):return json.loads(subprocess.check_output(['pactl','--format=json','list',kind],env=env,timeout=1,stderr=subprocess.DEVNULL))
        sources=read('sources');sinks=read('sinks');outputs=read('source-outputs')
        src=next((s for s in sources if s['name']==SOURCE),None)
        sink=next((s for s in sinks if s['name']==SINK),None)
        monitor=sink.get('monitor_source') if sink else None
        monitor_index=next((s['index'] for s in sources if s['name']==monitor),monitor)
        forwarded=any(str(o.get('source'))==str(monitor_index) and 'nomachine' in json.dumps(o.get('properties',{})).lower() for o in outputs)
        return {'available':bool(src and sink and forwarded and not src.get('mute') and not sink.get('mute')),
                'source':SOURCE,'sink':SINK,'remote_output_forwarded':forwarded,'kind':'NOMACHINE_DEVELOPMENT_BRIDGE'}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'available':False,'source':SOURCE,'sink':SINK,'error':'PULSE_ROUTE_UNAVAILABLE'}


def pcm_wave(pcm,rate=16000):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(pcm)
    return out.getvalue()


def transcribe(wav,cancel=None):
    from online_query import HOST
    key=os.environ.get('DASHSCOPE_API_KEY')
    if not key:raise ValueError('未配置在线密钥')
    body={'model':os.environ.get('ASR_MODEL','qwen3-asr-flash'),
        'messages':[{'role':'user','content':[{'type':'input_audio','input_audio':{
            'data':'data:audio/wav;base64,'+base64.b64encode(wav).decode()}}]}],
        'asr_options':{'language':'zh','enable_itn':True},'stream':False}
    req=urllib.request.Request(HOST+'/compatible-mode/v1/chat/completions',data=json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    payload=json.loads(request(req,15,cancel))
    text=payload['choices'][0]['message']['content']
    if not isinstance(text,str) or not 1<=len(text.strip())<=300:raise ValueError('ASR_EMPTY_OR_TOO_LONG')
    return text.strip()


def answer_text(result,mode):
    from safety_review import review
    if result.get('prompt_trial'):raise ValueError('提示词试答仅供人工评测，不进入语音引导')
    if mode!='STANDING':raise ValueError('请先站立观察再朗读详细回答')
    plan=result.get('plan') or {}
    if result.get('state')!='CANDIDATE' or result.get('safety_flags') or review(plan):raise ValueError('该回答需要人工核对或已经过期')
    if (plan.get('next_step') or {}).get('kind')=='move_candidate':raise ValueError('不朗读未经核验的移动指令')
    # Details are explicitly requested, never automatically queued in walking mode.
    text='画面理解，仅供确认。'+ '。'.join(o['fact'] for o in plan.get('observations',[])[:2])
    text+='。'+(plan.get('next_step') or {}).get('say','')
    if len(text)>350:raise ValueError('回答过长，请先缩短问题')
    return text


class VoiceIO:
    def __init__(self):
        self.lock=threading.RLock();self.stop_event=threading.Event();self.cancel_record=threading.Event()
        self.route={'available':False};self.status={'state':'IDLE','transcript':''}
        self.auto=False;self.pending=None;self.child=None;self.generation=0;self.priority=0
        self.cache={};self.last_event=None;self.guidance_mode=None;self.neural_lock=threading.Lock()
        self.synth_lock=threading.Lock()
        self.tts_process=None;self.prewarm_phase='CORE'
        self.worker=threading.Thread(target=self._worker,daemon=True);self.worker.start()
        threading.Thread(target=self._devices,daemon=True).start()
        threading.Thread(target=self._prewarm,daemon=True).start()

    def _devices(self):
        while not self.stop_event.is_set():
            route=devices()
            with self.lock:
                self.route=route
                if not route['available']:
                    self.interrupt();self.cancel_record.set()
            self.stop_event.wait(2)

    def read(self):
        with self.lock:return dict(self.status,route=dict(self.route),auto=self.auto,
            tts='LOCAL_PIPER_NEURAL_PLUS_ESPEAK_FALLBACK',
            tts_model=Path(piper_model_path()).name if piper_model_path() else None,
            asr='qwen3-asr-flash',speaking=self.child is not None,
            cache_phrases=len(self.cache),prewarm_phase=self.prewarm_phase)

    def _read_exact(self,stream,size,deadline):
        data=b''
        while len(data)<size:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('TTS worker timeout')
            ready,_,_=select.select([stream],[],[],remaining)
            if not ready:raise TimeoutError('TTS worker timeout')
            chunk=stream.read(size-len(data))
            if not chunk:raise RuntimeError('TTS worker stopped')
            data+=chunk
        return data

    def _neural_wav(self,text):
        model=piper_model_path()
        if not model:raise FileNotFoundError('Piper model unavailable')
        payload=text.encode('utf-8')
        if not 0<len(payload)<=4096:raise ValueError('Invalid TTS length')
        with self.neural_lock:
            if self.tts_process is None or self.tts_process.poll() is not None:
                env=dict(os.environ);env.pop('DASHSCOPE_API_KEY',None)
                env['LUMINA_PIPER_MODEL']=model
                self.tts_process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('piper_worker.py'))],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
            process=self.tts_process
            try:
                process.stdin.write(struct.pack('>I',len(payload))+payload);process.stdin.flush()
                deadline=time.monotonic()+8
                header=self._read_exact(process.stdout,4,deadline)
                size=struct.unpack('>I',header)[0]
                if not 0<size<=4*1024*1024:raise RuntimeError('Invalid TTS response')
                wav=self._read_exact(process.stdout,size,deadline)
                if not wav.startswith(b'RIFF') or wav[8:12]!=b'WAVE':raise RuntimeError('Invalid TTS WAV')
                return wav
            except Exception:
                if process.poll() is None:process.kill()
                process.wait()
                self.tts_process=None
                raise

    def _wav(self,text,prefer_neural=True):
        cache_key=('neural' if prefer_neural else 'espeak',text)
        with self.lock:wav=self.cache.get(cache_key)
        if wav is not None:return wav
        try:
            if prefer_neural:
                wav=self._neural_wav(text)
            else:
                with self.synth_lock:
                    with self.lock:wav=self.cache.get(cache_key)
                    if wav is not None:return wav
                    wav=subprocess.check_output([sys.executable,str(Path(__file__).with_name('local_tts.py'))],
                        input=text.encode(),timeout=4,stderr=subprocess.DEVNULL)
        except Exception:
            if prefer_neural:return self._wav(text,prefer_neural=False)
            raise
        with self.lock:
            if len(self.cache)>=96:self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key]=wav
        return wav

    def _wait_idle(self):
        while not self.stop_event.is_set():
            with self.lock:
                busy=self.priority>0 or self.pending is not None or self.child is not None
            if not busy:return True
            if self.stop_event.wait(.05):return False
        return False

    def _prewarm_batch(self,phrases,require_auto=False):
        for text in phrases:
            if self.stop_event.is_set():return False
            if require_auto:
                with self.lock:
                    if not self.auto:return False
            if not self._wait_idle():return False
            try:self._wav(text)
            except Exception:pass
            self.stop_event.wait(.05)
        return True

    def _prewarm(self):
        with self.lock:self.prewarm_phase='CORE'
        if not self._prewarm_batch(CORE_PREWARM_PHRASES):return
        with self.lock:self.prewarm_phase='IDLE'
        while not self.stop_event.is_set():
            with self.lock:enabled=self.auto
            if not enabled:
                self.stop_event.wait(.5);continue
            with self.lock:self.prewarm_phase='EXTRA'
            self._prewarm_batch(EXTRA_PREWARM_PHRASES,require_auto=True)
            with self.lock:self.prewarm_phase='READY'
            self.stop_event.wait(1)

    def interrupt(self):
        with self.lock:
            self.generation+=1;self.pending=None;self.priority=0
            if self.child and self.child.poll() is None:self.child.terminate()
            self.child=None

    def set_auto(self,value):
        if type(value) is not bool:raise ValueError('Invalid audio switch')
        with self.lock:
            self.auto=value;self.last_event=None
            if not value:self.interrupt()
        return self.read()

    def say(self,text,priority=10,valid=None):
        with self.lock:
            if not self.route.get('available'):raise ValueError('NoMachine音频路由不可用')
            if self.status['state'] in ('RECORDING','TRANSCRIBING'):raise ValueError('录音识别期间暂停播报')
            if priority<self.priority:return False
            self.interrupt();self.priority=priority
            self.pending=(text,self.generation,time.monotonic()+(3.5 if priority<90 else 2),valid,priority)
        return True

    def follow(self,g,valid=None):
        with self.lock:
            mode=g['mode'];key=(mode,g['event_id'],g.get('speech'))
            mode_changed=mode!=self.guidance_mode
            self.guidance_mode=mode
            if mode=='PAUSED' or (mode_changed and mode!='STANDING'):
                self.cancel_record.set();self.interrupt()
            if mode=='PAUSED':return
            if (g.get('priority',0)>=90 and self.priority<90
                    and (self.pending is not None or self.child is not None)):
                self.interrupt()
            if self.auto and key!=self.last_event and self.last_event is not None:self.interrupt()
            if self.auto and key!=self.last_event and g.get('speech'):
                try:
                    if self.say(g['speech'],g['priority'],valid=valid):self.last_event=key
                except ValueError:pass
            elif self.auto and not g.get('speech'):self.last_event=key

    def _current(self,generation,valid):
        with self.lock:
            if self.stop_event.is_set() or generation!=self.generation:return False
        try:return valid is None or bool(valid())
        except Exception:return False

    def _play_wav(self,wav,generation,expires,valid,env):
        """Recheck queued and playing speech without waiting for another guidance tick."""
        if time.monotonic()>expires or not self._current(generation,valid):return 'INVALIDATED'
        with self.lock:
            if generation!=self.generation:return 'INVALIDATED'
            child=subprocess.Popen(['paplay','--device='+SINK],env=env,stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.child=child;self.status['tts_state']='PLAYING'
        deadline=time.monotonic()+45;payload=wav
        try:
            while True:
                if not self._current(generation,valid):return 'INVALIDATED'
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Audio playback deadline')
                try:
                    child.communicate(payload,timeout=min(.1,remaining))
                    return 'FINISHED' if child.returncode==0 else 'INTERRUPTED_OR_FAILED'
                except subprocess.TimeoutExpired:payload=None
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.communicate(timeout=1)
                except subprocess.TimeoutExpired:child.kill();child.communicate()

    def _worker(self):
        env=dict(os.environ,PULSE_SERVER='unix:/run/user/1000/pulse/native',XDG_RUNTIME_DIR='/run/user/1000')
        while not self.stop_event.is_set():
            with self.lock:item=self.pending;self.pending=None
            if not item:self.stop_event.wait(.03);continue
            text,generation,expires,valid,priority=item
            try:
                with self.lock:
                    neural_cached=('neural',text) in self.cache
                    neural_ready=(self.tts_process is not None and self.tts_process.poll() is None)
                wav=self._wav(text,prefer_neural=neural_cached or priority<90 or neural_ready)
                state=self._play_wav(wav,generation,expires,valid,env)
                with self.lock:
                    if generation==self.generation:
                        self.status['tts_state']=state
                        if state=='FINISHED':self.status['last_spoken']=text
            except Exception as e:
                with self.lock:self.status['tts_error']=type(e).__name__
            finally:
                with self.lock:
                    if generation==self.generation:
                        if self.child and self.child.poll() is None:self.child.kill();self.child.wait()
                        self.child=None;self.priority=0

    def record(self,consent,seconds=5):
        if consent is not True:raise ValueError('请授权本次录音发送到百炼识别')
        with self.lock:
            if self.status['state'] in ('RECORDING','TRANSCRIBING'):raise ValueError('已有录音任务')
            if not self.route.get('available'):raise ValueError('NoMachine音频路由不可用')
            if not os.environ.get('DASHSCOPE_API_KEY'):raise ValueError('未配置在线密钥')
            self.interrupt();self.cancel_record=threading.Event();cancel=self.cancel_record
            self.status={'state':'RECORDING','transcript':'','seconds':5}
        threading.Thread(target=self._record,args=(cancel,),daemon=True).start()
        return self.read()

    def cancel(self):
        with self.lock:self.cancel_record.set();self.auto=False;self.interrupt()
        return self.read()

    def _record(self,cancel):
        child=None
        try:
            env=dict(os.environ,PULSE_SERVER='unix:/run/user/1000/pulse/native',XDG_RUNTIME_DIR='/run/user/1000')
            child=subprocess.Popen(['parec','--device='+SOURCE,'--format=s16le','--rate=16000','--channels=1','--raw'],
                env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not cancel.is_set():
                try:
                    child.communicate(timeout=.1)
                    raise RuntimeError('Audio input ended')
                except subprocess.TimeoutExpired:pass
            child.terminate();pcm,_=child.communicate(timeout=1)
            if cancel.is_set():raise InterruptedError('Cancelled')
            pcm=pcm[:16000*2*5];pcm=pcm[:len(pcm)//2*2]
            samples=np.frombuffer(pcm,np.int16).astype(np.float32)
            if len(samples)<8000 or float(np.sqrt(np.mean(samples*samples)))<80:raise ValueError('NO_SPEECH_LEVEL')
            with self.lock:self.status['state']='TRANSCRIBING'
            text=transcribe(pcm_wave(pcm),cancel)
            if cancel.is_set():raise InterruptedError('Cancelled')
            with self.lock:self.status.update(state='TRANSCRIBED',transcript=text)
        except Exception as e:
            with self.lock:self.status.update(state='CANCELLED' if cancel.is_set() else 'ERROR',error=type(e).__name__+': '+str(e)[:100])
        finally:
            if child and child.poll() is None:child.kill();child.communicate()

    def close(self):
        self.stop_event.set();self.cancel()
        with self.neural_lock:
            if self.tts_process and self.tts_process.poll() is None:self.tts_process.kill()
            if self.tts_process:self.tts_process.wait()
            self.tts_process=None
