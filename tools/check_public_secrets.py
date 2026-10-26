"""Check tracked/index content without ever printing matched credential values.

This small project-specific guard complements review. It is not a guarantee that
every possible secret format or private document can be detected automatically.
"""
import argparse
import fnmatch
import re
import subprocess
from pathlib import Path, PurePosixPath

CREDENTIAL_RULES = {
    'provider-token': re.compile(rb'\b(?:sk-[A-Za-z0-9_.-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|LTAI[A-Za-z0-9]{16,})'),
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'credential-literal': re.compile(rb'''(?i)\b(?:[a-z0-9_]*api[_-]?key|access[_-]?key[_-]?secret|password|passwd|secret[_-]?key|auth[_-]?token)\b["']?\]?\s*[:=]\s*["']([^"'\r\n]+)["']'''),
    'url-credentials': re.compile(rb'https?://[^/\s:@]+:[^/\s@]+@'),
}
PRIVATE_RULES = {
    'workspace-endpoint': re.compile(rb'\bllm-[a-z0-9]+\.[a-z0-9-]+\.maas\.aliyuncs\.com'),
    'camera-serial': re.compile(rb'\bCPC[A-Z0-9]{6,}\b'),
    'local-account-path': re.compile(rb'(?i)[c-z]:[\\/]+Users[\\/]+(?!Public\b|your[-_]user\b|example\b)[^\s"\'<>]+|/home/(?!your-user\b|example\b)[a-zA-Z0-9_-]+/'),
}
PLACEHOLDERS = {b'test-only', b'test', b'test-key', b'dummy', b'fake', b'YOUR_API_KEY',
                b'YOUR_PASSWORD', b'[REDACTED_SECRET]', b'<API_KEY>', b'<PASSWORD>'}
PRIVATE_DIRS = {'.ssh', '.aws', '.local-tools', 'runtime', 'backups', 'captures',
                'recordings', 'evidence', 'exports'}
PRIVATE_NAMES = ('.env', '.env.*', '*credentials*.json', '*credentials*.yaml',
                 '*credentials*.yml', '*secrets*.json', '*secrets*.yaml',
                 '*secrets*.yml', '*.pem', '*.key', '*.p12', '*.pfx', '*.db',
                 '*.db-*', '*.sqlite*', '*.log', '*.bundle', '*.zip', '*.tar.gz',
                 '*.7z', '*.onnx', '*.pt', '*.pth', '*.wav', '*.mp4', '*.mkv')


def scan_content(data, credentials_only=False):
    rules = dict(CREDENTIAL_RULES)
    if not credentials_only:
        rules.update(PRIVATE_RULES)
    found = []
    for rule, pattern in rules.items():
        for match in pattern.finditer(data):
            if rule == 'credential-literal' and match.group(1) in PLACEHOLDERS:
                continue
            found.append((rule, data[:match.start()].count(b'\n') + 1))
    return found


def private_path(name):
    path = PurePosixPath(name)
    return (bool(set(path.parts) & PRIVATE_DIRS)
            or (path.name != '.env.example'
                and any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in PRIVATE_NAMES)))


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def scan_repository(root, staged=False, history=False):
    found = []
    args = ['ls-files', '-z'] if staged else ['ls-files', '-z', '--cached', '--others', '--exclude-standard']
    for raw in sorted(set(filter(None, git(root, *args).split(b'\0')))):
        name = raw.decode('utf-8')
        if private_path(name):
            found.append((name, 'private-file', 1))
        path = root / name
        if staged:
            data = git(root, 'show', ':' + name)
        elif path.is_file():
            data = path.read_bytes()
        else:
            continue
        found.extend((name, rule, line) for rule, line in scan_content(data))
    if history:
        objects = git(root, 'rev-list', '--objects', '--all').splitlines()
        child = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for row in objects:
                oid, _, raw_name = row.partition(b' ')
                child.stdin.write(oid + b'\n'); child.stdin.flush()
                header = child.stdout.readline().split()
                if len(header) != 3:
                    raise RuntimeError('Cannot inspect Git object')
                kind, size = header[1], int(header[2])
                data = child.stdout.read(size)
                if len(data) != size or child.stdout.read(1) != b'\n':
                    raise RuntimeError('Incomplete Git object')
                if kind == b'blob':
                    label = 'history/' + oid[:12].decode() + '/' + raw_name.decode('utf-8')
                    found.extend((label, rule, line) for rule, line in scan_content(data, credentials_only=True))
        finally:
            child.stdin.close(); child.stdout.close(); child.wait(timeout=10)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--staged', action='store_true', help='Check exact Git index content')
    parser.add_argument('--history', action='store_true', help='Also check credential patterns in all reachable historical blobs')
    args = parser.parse_args(argv)
    findings = scan_repository(args.root.resolve(), args.staged, args.history)
    for name, rule, line in findings:
        print(f'{name}:{line}: {rule} [value hidden]')
    print(f'Public content check: {len(findings)} finding(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
