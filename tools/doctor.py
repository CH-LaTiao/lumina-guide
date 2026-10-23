"""Read-only environment evidence. Does not read credentials or capture images."""
import json, os, platform, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def command(args):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=10)
  return {'returncode':r.returncode,'stdout':r.stdout.strip(),'stderr':r.stderr.strip()}
 except (OSError,subprocess.TimeoutExpired) as e:return {'error':type(e).__name__}
def report():
 usb=command(['lsusb']); tree=command(['lsusb','-t']);free=shutil.disk_usage(Path.home()).free
 return {'utc':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
 'python':sys.version,'free_disk_bytes':free,'disk_warning':free<20*1024**3,
 'l4t':Path('/etc/nv_tegra_release').read_text() if Path('/etc/nv_tegra_release').exists() else None,
 'usb':usb,'usb_tree':tree,'camera_usb_present':'2bc5:0807' in usb.get('stdout',''),
 'camera_stream_validated':False,'tools':{t:shutil.which(t) for t in ['cmake','g++','git','python3']},
 'audio_capture':command(['arecord','-l']),'audio_playback':command(['aplay','-l']),
 'orbbec_sdk':sdk_paths(),'frame_server':str(ROOT/'build/frame-server') if (ROOT/'build/frame-server').is_file() else None,
 'shm_ring':{'path':'/dev/shm/lumina-frames-v1','present':Path('/dev/shm/lumina-frames-v1').exists()},
 'deploy_current':os.readlink(Path.home()/'lumina-deploy'/'current') if (Path.home()/'lumina-deploy'/'current').is_symlink() else None}
def sdk_paths():
 libs=sorted((Path.home()/'orbbec-sdk').glob('*/lib/libOrbbecSDK.so'))
 if not libs:return {'lib':None,'extensions':None}
 lib=libs[-1]
 return {'lib':str(lib),'extensions':str(lib.parent/'extensions')}
if __name__=='__main__':print(json.dumps(report(),ensure_ascii=False,indent=2))
