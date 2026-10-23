"""Snapshot of the actual source loaded at process start."""
import json
import subprocess
from pathlib import Path

VERSION='0.9.9'

def identity():
    root=Path(__file__).resolve().parents[1]
    result={'version':VERSION,'code_root':str(root),'commit':None,'dirty':None}
    if (root/'manifest.json').is_file():
        manifest=json.loads((root/'manifest.json').read_text())
        result.update(version=manifest['version'],commit=manifest.get('commit'),dirty=False,source='release')
    else:
        try:
            result.update(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True,timeout=2).strip(),
                dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True,timeout=2).strip()),source='working-tree')
        except (OSError,subprocess.SubprocessError):result['source']='unknown'
    return result
