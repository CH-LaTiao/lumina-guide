"""Manual Jetson desktop startup. No boot service, shell interpolation or saved API key."""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'


def control(*args, env=None):
    result = subprocess.run([sys.executable, str(ROOT/'tools/preview_control.py'), *args],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=40)
    if result.returncode:
        # Never print the child's environment or a credential-bearing command.
        raise RuntimeError('启动控制失败，请查看 runtime/live-view.log 与 frame-server.log')
    return json.loads(result.stdout)


def read_status():
    with urllib.request.urlopen('http://127.0.0.1:8765/status', timeout=3) as response:
        return json.load(response)


def wait_ready(timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = read_status()
            if value.get('software'):
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    raise RuntimeError('服务未在45秒内启动，请查看 runtime/live-view.log')


def owned_key(record):
    # Only call after preview_control has checked this PID's exact executable path.
    try:
        for entry in Path(f"/proc/{record['pid']}/environ").read_bytes().split(b'\0'):
            if entry.startswith(b'DASHSCOPE_API_KEY='):
                return entry.split(b'=', 1)[1].decode()
    except (OSError, ValueError):
        pass
    return None


def ensure_running():
    import fcntl
    from preview_control import select_source
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'desktop-start.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source, _ = select_source(True)
        lan = (source/'tools/lan_access.py').is_file()
        before = control('status')
        record = before.get('process') or {}
        if before['running'] and Path(record['code_root']).resolve() == source and bool(record.get('lan')) == lan:
            return wait_ready(), False
        env = dict(os.environ, LUMINA_FRAME_SOURCE='shm')
        if before['running']:
            key = owned_key(record)
            if key:
                env['DASHSCOPE_API_KEY'] = key
            control('stop')
        args = ['start', '--deployed'] + (['--lan'] if lan else [])
        try:
            control(*args, env=env)
            return wait_ready(), True
        except Exception:
            # A failed candidate must not destroy the ability to use the previous release.
            control('stop')
            version = record.get('version', '')
            if version.startswith('v'):
                subprocess.run([sys.executable, str(ROOT/'tools/releases.py'), 'switch', version],
                               cwd=ROOT, capture_output=True, check=True, timeout=15)
                control('start', '--deployed', *(['--lan'] if record.get('lan') else []), env=env)
            raise


def desktop_text(script, name='一键启动灵犀', action='launch'):
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\n'
            f'Name={name}\nComment=启动实时画面、识别算法与运行日志\n'
            f'Exec=gnome-terminal --title="灵犀 · 启动与实时状态" -- /usr/bin/python3 -u {quote(script)} {action}\n'
            'Terminal=false\nIcon=camera-video\nStartupNotify=false\nCategories=Utility;\n'
            'Actions=Stop;\n\n[Desktop Action Stop]\nName=停止灵犀程序\n'
            f'Exec=gnome-terminal -- /usr/bin/python3 -u {quote(script)} stop\n')


def install():
    result = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True, check=True)
    desktop = Path(result.stdout.strip()).resolve()
    if not desktop.is_relative_to(Path.home().resolve()):
        raise RuntimeError('桌面目录不在当前用户目录内，停止写入')
    desktop.mkdir(exist_ok=True)
    target = desktop/'Lumina-Start.desktop'
    text = desktop_text(Path(__file__).resolve())
    if target.exists() and target.read_text(encoding='utf-8') != text:
        backup = RUNTIME/'desktop-backups'; backup.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copy2(target, backup/f'Lumina-Start-{stamp}.desktop')
    target.write_text(text, encoding='utf-8'); target.chmod(0o755)
    trusted = subprocess.run(['gio', 'set', str(target), 'metadata::trusted', 'true'],
                             capture_output=True, text=True).returncode == 0
    print(json.dumps({'desktop':str(target), 'trusted':trusted}, ensure_ascii=False))


def launch(open_browser=True, console=True):
    print('正在启动灵犀……', flush=True)
    status, started = ensure_running()
    print('程序已启动。' if started else '程序已在运行，直接打开现有画面。', flush=True)
    if not status.get('online', {}).get('configured'):
        print('在线密钥未载入；本地识别、深度画面和本地语音可用。在线问答与ASR待配置。', flush=True)
    if open_browser:
        env = dict(os.environ); env.pop('DASHSCOPE_API_KEY', None)
        subprocess.Popen(['xdg-open', 'http://127.0.0.1:8765/'], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if console:
        from status_console import main as show_console
        show_console()
    return {'started':started, 'version':status.get('software_version'), 'network':status.get('network')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['launch', 'install', 'stop'], nargs='?', default='launch')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-console', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'install': install()
        elif args.action == 'stop':
            control('stop'); print('灵犀程序已停止。', flush=True)
        else:
            result = launch(not args.no_browser, not args.no_console)
            if args.no_console: print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print('启动未完成：' + str(exc), flush=True)
        if sys.stdin.isatty(): input('按回车关闭窗口。')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
