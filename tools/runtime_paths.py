"""Keep mutable data outside an immutable release when launched by preview_control."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def runtime_dir():
    path = Path(os.environ.get('LUMINA_RUNTIME_DIR', ROOT / 'runtime')).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
