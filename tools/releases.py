"""Install immutable, checksummed releases. Switching does not start hardware or services."""
import argparse, hashlib, json, os, shutil, subprocess, tarfile, tempfile
from pathlib import Path
from release_manifest import verify_release
p=argparse.ArgumentParser();p.add_argument('action',choices=['save','switch','verify']);p.add_argument('version');p.add_argument('--root',type=Path,default=Path.home()/'lumina-deploy');a=p.parse_args()
if not a.version or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for x in a.version) or a.version in ('.','..'):p.error('invalid version')
src=Path(__file__).resolve().parents[1];base=a.root.resolve();base.mkdir(parents=True,exist_ok=True);releases=base/'releases';releases.mkdir(exist_ok=True);dest=releases/a.version
def digest(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def verify(d):
 return verify_release(d)
if a.action=='save':
 if dest.exists():raise SystemExit('Release exists; will not overwrite')
 if subprocess.check_output(['git','status','--porcelain'],cwd=src,text=True).strip():raise SystemExit('Commit changes before release')
 tag_exists=subprocess.run(['git','show-ref','--verify','--quiet','refs/tags/'+a.version],cwd=src).returncode==0
 if tag_exists:raise SystemExit('Git tag exists; choose a new release version')
 archive=base/(a.version+'.tar.gz')
 if archive.exists():raise SystemExit('Release archive exists; choose a new release version')
 bundle=base/(a.version+'.bundle')
 if bundle.exists():raise SystemExit('Git bundle exists; choose a new release version')
 work=Path(tempfile.mkdtemp(prefix='.release-',dir=base));tmp=work/'release'
 archive_tmp=work/archive.name;bundle_tmp=work/bundle.name;tag_created=False
 try:
  files=subprocess.check_output(['git','ls-files','-z'],cwd=src).decode().split('\0')
  for name in filter(None,files):
   f=src/name;t=tmp/name;t.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,t)
  binary=src/'build/lumina-core'
  if not binary.is_file():raise SystemExit('Build first')
  (tmp/'bin').mkdir();shutil.copy2(binary,tmp/'bin/lumina-core')
  native=src/'build/liblumina-depth.so'
  if native.exists():shutil.copy2(native,tmp/'bin/liblumina-depth.so')
  frame_server=src/'build/frame-server'
  if frame_server.exists():shutil.copy2(frame_server,tmp/'bin/frame-server')
  commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=src,text=True).strip()
  manifest={'version':a.version,'commit':commit,'files':{f.relative_to(tmp).as_posix():digest(f) for f in tmp.rglob('*') if f.is_file()}}
  (tmp/'manifest.json').write_text(json.dumps(manifest,indent=2));verify(tmp)
  with tarfile.open(archive_tmp,'w:gz') as tar:tar.add(tmp,arcname=a.version)
  subprocess.check_call(['git','tag',a.version,commit],cwd=src);tag_created=True
  subprocess.check_call(['git','bundle','create',str(bundle_tmp),'--all'],cwd=src)
  heads=subprocess.check_output(['git','bundle','list-heads',str(bundle_tmp)],cwd=src,text=True)
  if 'refs/tags/'+a.version not in heads:raise RuntimeError('Git bundle does not contain the release tag')
  tmp.rename(dest);archive_tmp.replace(archive);bundle_tmp.replace(bundle)
 except BaseException:
  if tag_created:subprocess.run(['git','tag','-d',a.version],cwd=src,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if dest.exists():shutil.rmtree(dest)
  for path in (archive,bundle):
   if path.exists():path.unlink()
  raise
 finally:shutil.rmtree(work,ignore_errors=True)
 print(json.dumps({'release':str(dest),'archive':str(archive),'sha256':digest(Path(archive)),'tag':a.version,
                   'bundle':str(bundle),'bundle_sha256':digest(bundle)}))
else:
 verify(dest)
 if a.action=='switch':
  link=base/'current'
  if link.exists() and not link.is_symlink():raise SystemExit('current is not a symlink')
  previous=os.readlink(link) if link.is_symlink() else None
  temporary=base/('.current-'+str(os.getpid()));temporary.symlink_to(dest,target_is_directory=True);temporary.replace(link)
  with (base/'switches.jsonl').open('a') as f:f.write(json.dumps({'from':previous,'to':str(dest)})+'\n')
 print('VERIFIED '+str(dest))
