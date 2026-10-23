"""Read-only, human-facing live console. No cloud calls or sensor ownership."""
import datetime
import json
import time
import urllib.request


def access_banner(status):
    network = status.get('network') or {}
    if not network.get('lan'):return None
    return ('电脑/手机浏览器访问：' + '  '.join(network.get('urls') or [])
            + '\n同一局域网直接访问，无需访问码。'
            + '\n开发板自己的浏览器：http://127.0.0.1:8765/')


def line(s):
    detection = s.get('detection', {})
    guidance = s.get('guidance', {})
    readiness = s.get('readiness', {})
    corridors = ' '.join(c['direction'][0]+':'+c['state'].replace('OBSERVED_CLEAR_CANDIDATE','CANDIDATE') for c in guidance.get('corridors',[]))
    speech = guidance.get('speech') or (s.get('feedback', {}).get('speech') or {}).get('text') or '无新提示'
    blockers = readiness.get('blockers') or []
    blocker_text = ('无' if not blockers else f"{len(blockers)}项：" + '；'.join(blockers[:2]))
    return (f"{s.get('state', '?')} | 采集 {s.get('camera_fps', '?')} fps | "
            f"帧龄 {s.get('frame_age_ms', '?')} ms | {detection.get('provider', '?')} "
            f"检测 {len(detection.get('objects', []))} 个 | 模式 {guidance.get('mode', '待升级')} | "
            f"建议 {guidance.get('action', '?')} | {speech} | "
            f"就绪 {readiness.get('stage', '?')} | 阻塞 {blocker_text} | "
            f"在线 {s.get('online', {}).get('state', '?')} | "
            f"语音 {s.get('audio', {}).get('state', '待接入')} | {corridors}")


def main():
    print('灵犀实时运行窗口 · 每2秒更新 · 关闭本窗口不影响程序', flush=True)
    print('桌面测试阶段；方向候选不代表已验收的通行路线。', flush=True)
    last_banner = None
    while True:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/status', timeout=2) as r:
                s = json.load(r)
            banner = access_banner(s)
            if banner and banner != last_banner:
                print('\n'+banner+'\n', flush=True);last_banner = banner
            message = line(s)
        except Exception as e:
            message = '等待服务连接：' + type(e).__name__
        print(datetime.datetime.now().strftime('%H:%M:%S') + ' | ' + message, flush=True)
        time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
