"""Exercise rollback/corruption rejection and checkpoint skip using temporary state."""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Recovery(unittest.TestCase):
 def test_release_and_checkpoint(self):
  with tempfile.TemporaryDirectory() as t:
   r=Path(t)/'repo';shutil.copytree(ROOT,r,ignore=shutil.ignore_patterns('.git','runtime','__pycache__'))
   def run(*args,ok=True):
    p=subprocess.run(args,cwd=r,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if ok:self.assertEqual(p.returncode,0,p.stdout+p.stderr)
    else:self.assertNotEqual(p.returncode,0)
    return p
   run('git','init');run('git','add','.');run('git','-c','user.name=Test','-c','user.email=test@localhost','commit','-m','fixture')
   (r/'build').mkdir(exist_ok=True);(r/'build/lumina-core').write_bytes(b'test-binary')
   base=Path(t)/'deploy';script=str(r/'tools/releases.py')
   for v in ('v1','v2'):run(sys.executable,script,'save',v,'--root',str(base))
   head=run('git','rev-parse','HEAD').stdout.strip()
   for v in ('v1','v2'):
    self.assertEqual(run('git','rev-parse',v).stdout.strip(),head)
    bundle=base/(v+'.bundle');self.assertTrue(bundle.is_file());self.assertGreater(bundle.stat().st_size,0)
    self.assertIn('refs/tags/'+v,run('git','bundle','list-heads',str(bundle)).stdout)
   before=sorted(str(p.relative_to(base)) for p in base.rglob('*'))
   run(sys.executable,script,'save','v1','--root',str(base),ok=False)
   self.assertEqual(before,sorted(str(p.relative_to(base)) for p in base.rglob('*')))
   if os.name=='posix':
    for v in ('v1','v2','v1'):
     run(sys.executable,script,'switch',v,'--root',str(base));self.assertEqual((base/'current').resolve(),base/'releases'/v)
     # The launcher must select the same version, not the working directory.
     code="import sys;from pathlib import Path;sys.path.insert(0,'tools');from preview_control import select_source;print(select_source(True,deploy=Path(sys.argv[1]))[0])"
     self.assertEqual(run(sys.executable,'-c',code,str(base)).stdout.strip(),str(base/'releases'/v))
    (base/'releases/v2/bin/lumina-core').write_bytes(b'corrupt')
    run(sys.executable,script,'switch','v2','--root',str(base),ok=False)
    self.assertEqual((base/'current').resolve(),base/'releases/v1')
   evidence=r/'evidence.json';evidence.write_text('{}');cp=str(r/'tools/checkpoint.py')
   run(sys.executable,cp,'done','--step','test','--evidence',str(evidence))
   self.assertIn('DONE_SKIP',run(sys.executable,cp,'resume').stdout)
   evidence.write_text('{"changed":true}')
   self.assertIn('EVIDENCE_CHANGED_RECHECK',run(sys.executable,cp,'resume').stdout)
if __name__=='__main__':unittest.main()
