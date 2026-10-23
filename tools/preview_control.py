"""Launch source or a verified release; preserve runtime data across switches (Jetson/Linux)."""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from release_manifest import verify_release

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = Path.home() / 'lumina-deploy'
SERIAL = os.environ.get('LUMINA_SERIAL', 'YOUR_DEVICE_SERIAL')
MODEL = Path(os.environ.get('LUMINA_YOLO_MODEL',
    str(Path.home() / 'ultralytics/ultralytics/yolo11n.onnx')))
SDK_PYTHON = Path(os.environ.get('LUMINA_SDK_PYTHON',
    str(Path.home() / 'lumina-sdk-env/bin/python')))

def select_source(deployed, root=ROOT, deploy=DEPLOY):
    if not deployed:
        return root, None
    source = (deploy / 'current').resolve()
    if not source.is_relative_to((deploy / 'releases').resolve()):
        raise ValueError('current does not point inside releases')
    return source, verify_release(source)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['start','stop','status','frame-start','frame-stop'])
    p.add_argument('--key-stdin', action='store_true')
    p.add_argument('--deployed', action='store_true', help='Launch verified lumina-deploy/current')
    p.add_argument('--lan', action='store_true', help='Allow direct LAN browser access without a login')
    a = p.parse_args()
    runtime = ROOT / 'runtime';runtime.mkdir(exist_ok=True)
    pidfile = runtime / 'preview.pid'
    frame_pidfile = runtime / 'frame-server.pid'
    if a.key_stdin:
        key = sys.stdin.readline().strip()
        if not key or len(key) > 1024:
            raise SystemExit('Invalid in-memory credential input')
        os.environ['DASHSCOPE_API_KEY'] = key

    def existing():
        if not pidfile.exists():return None
        raw = json.loads(pidfile.read_text())
        record = {'pid': raw, 'code_root': str(ROOT), 'version':'working-tree'} if isinstance(raw,int) else raw
        pid = int(record['pid']);source = Path(record['code_root']).resolve()
        if source != ROOT and not source.is_relative_to((DEPLOY/'releases').resolve()):
            raise SystemExit('Unexpected recorded code root; refusing signal')
        proc = Path(f'/proc/{pid}/cmdline')
        if not proc.exists():return None
        try:args = proc.read_bytes().split(b'\0')
        except FileNotFoundError:return None
        if not any(args):return None
        if str(source/'tools/live_view.py').encode() not in args:
            raise SystemExit('PID no longer belongs to this preview; refusing signal')
        return record

    def frame_record():
        if not frame_pidfile.exists():return None
        try:pid = int(frame_pidfile.read_text().strip())
        except ValueError:return None
        proc = Path(f'/proc/{pid}/cmdline')
        if not proc.exists():return None
        try:args = proc.read_bytes()
        except OSError:return None
        if b'frame-server' not in args:return None
        return {'pid':pid}

    def stop_frame_server():
        record = frame_record()
        if not record:return
        os.kill(record['pid'],signal.SIGTERM)
        for _ in range(50):
            if not frame_record():break
            time.sleep(.1)

    def start_frame_server(env,source=None):
        if SERIAL == 'YOUR_DEVICE_SERIAL':
            raise SystemExit('Set LUMINA_SERIAL to the Orbbec camera serial')
        candidates=([source/'bin/frame-server'] if source else [])+[
            ROOT/'build/frame-server',DEPLOY/'current/bin/frame-server']
        server=next((path for path in candidates if path.exists()),None)
        if server is None:
            raise SystemExit('shm mode needs build/frame-server; build with -DLUMINA_ORBBEC_SDK')
        extensions = env.get('LUMINA_ORBBEC_EXTENSIONS') or ''
        if not extensions:
            candidates = sorted((Path.home()/'orbbec-sdk').glob('*/lib/extensions'))
            if not candidates:raise SystemExit('set LUMINA_ORBBEC_EXTENSIONS to the Orbbec SDK extensions directory')
            extensions = str(candidates[-1])
        frame_env = dict(env,LUMINA_ORBBEC_EXTENSIONS=extensions)
        with (runtime/'frame-server.log').open('ab') as log:
            child = subprocess.Popen([str(server),SERIAL],env=frame_env,stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,start_new_session=True)
        frame_pidfile.write_text(str(child.pid))
        time.sleep(2)
        if child.poll() is not None:raise SystemExit('Frame server failed; inspect runtime/frame-server.log')
        return {'pid':child.pid}

    with (runtime/'preview-control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = existing()
        if a.action == 'frame-start':
            if not frame_record():start_frame_server(dict(os.environ))
        elif a.action == 'frame-stop':
            stop_frame_server()
        elif a.action == 'stop':
            if record:
                os.kill(record['pid'], signal.SIGTERM)
                for _ in range(70):
                    if not existing():break
                    time.sleep(.1)
                if existing():raise SystemExit('Preview did not stop; inspect before restart')
            stop_frame_server()
        elif a.action == 'start' and not record:
            if SERIAL == 'YOUR_DEVICE_SERIAL':
                raise SystemExit('Set LUMINA_SERIAL to the Orbbec camera serial')
            source, manifest = select_source(a.deployed)
            if a.lan and not (source/'tools/lan_access.py').is_file():
                raise SystemExit('Selected release does not support LAN access; start without --lan')
            env = dict(os.environ, LUMINA_RUNTIME_DIR=str(runtime))
            frame = None
            if env.get('LUMINA_FRAME_SOURCE') == 'shm' and not frame_record():
                frame = start_frame_server(env, source)
            with (runtime/'live-view.log').open('ab') as log:
                child = subprocess.Popen([str(SDK_PYTHON),str(source/'tools/live_view.py'),
                    '--serial',SERIAL,'--model',str(MODEL)]
                    +(['--host','0.0.0.0'] if a.lan else []),
                    cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            record = {'pid':child.pid,'code_root':str(source),'version':manifest['version'] if manifest else 'working-tree',
                      'commit':manifest.get('commit') if manifest else None, 'runtime':str(runtime),'lan':a.lan}
            temporary=pidfile.with_suffix('.tmp');temporary.write_text(json.dumps(record));temporary.replace(pidfile)
            time.sleep(1)
            if child.poll() is not None:raise SystemExit('Startup failed; inspect runtime/live-view.log')
        record = existing()
        print(json.dumps({'running':record is not None, 'process':record, 'frame_server':frame_record()}))

if __name__ == '__main__':main()
