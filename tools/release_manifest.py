"""Shared release verification for switching and actual process launch."""
import hashlib
import json

def verify_release(directory):
    directory = directory.resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest.get('files'), dict) or not manifest['files']:
        raise ValueError('Empty release manifest')
    for name, sha in manifest['files'].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError('Release checksum failed: ' + name)
    for name in ('tools/live_view.py','tools/live_view.html','bin/lumina-core'):
        if name not in manifest['files']:
            raise ValueError('Required release file missing: ' + name)
    return manifest
