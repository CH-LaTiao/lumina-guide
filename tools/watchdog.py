"""Minimal process watchdog for the two-process preview architecture.

It restarts the C++ frame server (shm mode) and the preview service when either
is missing. It does not install a boot service, and a restarted preview starts
without the online key because credentials are only ever held in process memory.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime'


def decide(preview_alive,frame_alive,shm_mode):
    actions=[]
    if shm_mode and not frame_alive:
        actions.append('frame-start')
    if not preview_alive:
        actions.append('start')
    return actions


def log(line):
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'watchdog.log').open('a',encoding='utf-8') as handle:
        handle.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")


def status():
    result=subprocess.run([sys.executable,str(ROOT/'tools/preview_control.py'),'status'],
        capture_output=True,text=True,timeout=30)
    if result.returncode!=0:raise RuntimeError(result.stderr.strip() or 'status failed')
    return json.loads(result.stdout)


def run_action(action):
    result=subprocess.run([sys.executable,str(ROOT/'tools/preview_control.py'),action],
        capture_output=True,text=True,timeout=60)
    if result.returncode!=0:raise RuntimeError(result.stderr.strip() or action+' failed')
    return result.stdout.strip()


def frames_arrived(timeout=10):
    from frame_shm import FrameShm
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:reader=FrameShm()
        except (OSError,ValueError):
            time.sleep(.5);continue
        try:
            if reader.latest() is not None:return True
        finally:reader.close()
        time.sleep(.5)
    return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--interval',type=float,default=10)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    shm_mode=os.environ.get('LUMINA_FRAME_SOURCE','sdk')=='shm'
    while True:
        try:
            state=status()
            actions=decide(bool(state.get('running')),bool(state.get('frame_server')),shm_mode)
            for action in actions:
                log(f'missing process, running {action}')
                run_action(action)
                log(f'{action} completed')
                if action=='frame-start' and not frames_arrived():
                    log('frame server has no frames; USB may be wedged. Recovery: '
                        'preview_control.py frame-stop -> sudo python3 tools/usb_reset.py -> '
                        'preview_control.py frame-start')
        except Exception as exc:
            log(f'watchdog error {type(exc).__name__}: {exc}')
        if args.once:return
        time.sleep(max(1,args.interval))


if __name__=='__main__':main()
