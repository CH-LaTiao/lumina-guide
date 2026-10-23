"""Measure the fully-local Piper/eSpeak path without playing audio."""
import argparse
import io
import json
import os
import select
import statistics
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

PHRASES=('前方可走。','停，改走左侧。','停，有台阶。','停，感知不可用。')


def read_exact(stream,size,deadline):
    data=b''
    while len(data)<size:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('TTS response timeout')
        ready,_,_=select.select([stream],[],[],remaining)
        if not ready:raise TimeoutError('TTS response timeout')
        chunk=stream.read(size-len(data))
        if not chunk:raise RuntimeError('TTS worker stopped')
        data+=chunk
    return data


def wav_seconds(payload):
    with wave.open(io.BytesIO(payload),'rb') as wav:
        if wav.getnchannels()!=1 or wav.getsampwidth()!=2:raise ValueError('Unexpected WAV format')
        return wav.getnframes()/wav.getframerate()


def synthesize(process,text,timeout=8):
    payload=text.encode('utf-8')
    started=time.monotonic()
    process.stdin.write(struct.pack('>I',len(payload))+payload)
    process.stdin.flush()
    deadline=started+timeout
    header=read_exact(process.stdout,4,deadline)
    size=struct.unpack('>I',header)[0]
    if not 0<size<=4*1024*1024:raise RuntimeError('Invalid WAV size')
    wav=read_exact(process.stdout,size,deadline)
    if not wav.startswith(b'RIFF') or wav[8:12]!=b'WAVE':raise RuntimeError('Invalid WAV')
    return {'latency_s':round(time.monotonic()-started,3),'audio_s':round(wav_seconds(wav),3)}


def p95(values):
    ordered=sorted(values)
    return ordered[max(0,-(-len(ordered)*95//100)-1)]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-warm-latency',type=float,default=1.)
    args=parser.parse_args()
    if args.output.exists():parser.error('Evidence exists; choose a new name')
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ);env.pop('DASHSCOPE_API_KEY',None)
    process=subprocess.Popen([sys.executable,str(root/'tools'/'piper_worker.py')],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    piper_rows=[]
    error=None
    try:
        for text in PHRASES:piper_rows.append({'text':text,**synthesize(process,text)})
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc)[:200]
    finally:
        if process.poll() is None:
            try:process.stdin.close()
            except OSError:pass
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
    warm=[row['latency_s'] for row in piper_rows[1:]]
    espeak=None;espeak_error=None
    started=time.monotonic()
    try:
        payload=subprocess.check_output([sys.executable,str(root/'tools'/'local_tts.py')],
            input=PHRASES[0].encode(),timeout=5,stderr=subprocess.PIPE,env=env)
        espeak={'latency_s':round(time.monotonic()-started,3),'audio_s':round(wav_seconds(payload),3)}
    except Exception as exc:
        espeak_error=type(exc).__name__+': '+str(exc)[:200]
    checks={
        'piper_completed':len(piper_rows)==len(PHRASES) and error is None,
        'warm_latency':bool(warm) and p95(warm)<=args.max_warm_latency,
        'espeak_completed':espeak is not None,
    }
    report={'mode':'LOCAL_ONLY','piper_model':os.environ.get('LUMINA_PIPER_MODEL'),
        'length_scale':float(os.environ.get('LUMINA_PIPER_LENGTH_SCALE','0.80')),
        'piper':{'cold_start_s':piper_rows[0]['latency_s'] if piper_rows else None,
            'phrases':piper_rows,
            'warm_latency_median_s':round(statistics.median(warm),3) if warm else None,
            'warm_latency_p95_s':round(p95(warm),3) if warm else None,
            'max_warm_latency_s':args.max_warm_latency,
            'error':error},
        'espeak':{**(espeak or {}),'rate':205,'error':espeak_error},
        'checks':checks,'passed':all(checks.values()),'cloud_calls':0,'audio_saved':0}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':checks,
        'piper_warm_p95_s':report['piper']['warm_latency_p95_s'],
        'piper_cold_start_s':report['piper']['cold_start_s']},ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
