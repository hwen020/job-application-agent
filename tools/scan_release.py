"""Scan the current index and all reachable Git snapshots without echoing matches."""
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {
    '__init__', 'jd', 'materials', 'layout', 'imports', 'review', 'storage', 'sync',
    'material_schema', 'runner_schema', 'runner', 'runner_fixtures', 'runner_browser',
    'runner_vault', 'providers', 'seed', 'server',
}
FILES = {f'app/{name}.py' for name in SOURCE} | {
    '.dockerignore', '.env.example', '.gitignore', '.github/workflows/ci.yml',
    'README.md', 'ARCHITECTURE.md', 'DEMO_GUIDE.md', 'SECURITY.md', 'LICENSE-PENDING.md', 'LICENSE',
    'requirements.txt', 'start.ps1', 'Dockerfile', 'render.yaml', 'docs/dashboard.png', 'docs/RELEASE_VALIDATION.md',
    'app/static/index.html', 'app/static/style.css', 'app/static/app.js', 'app/static/runner-fixture.js',
    'tests/test_demo.py', 'tools/scan_release.py',
}
CHECKS = {
    'private key': r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----',
    'token or OAuth identifier': r'(?i)\b(?:GOCSPX-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}|[0-9]{8,}-[a-z0-9]+\.apps\.googleusercontent\.com)\b',
    'local path': r'(?i)(?:[a-z]:[\\/](?:users|downloads|documents|desktop)[\\/]|/(?:home|users)/[a-z0-9_-]+)',
    'possible application identifier': r'(?i)\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b',
}
EMAIL = re.compile(r'[\w.+%-]+@[\w.-]+\.[a-zA-Z]{2,}')
PHONE = re.compile(r'(?<!\d)(?:\+1[- .])?(?:\(\d{3}\)|\d{3})[- .]\d{3}[- .]\d{4}(?!\d)')
URL = re.compile(r'https?://([a-z0-9.-]+)', re.I)


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory='+ROOT.as_posix(), *args], cwd=ROOT)


def check_image(raw):
    if not raw.startswith(bytes.fromhex('89504e470d0a1a0a')) or len(raw)>2_000_000:
        raise ValueError('invalid or oversized screenshot')
    offset = 8
    while offset+12<=len(raw):
        length, kind = struct.unpack_from('>I4s', raw, offset)
        offset += length+12
        if kind not in (b'IHDR', b'IDAT', b'IEND', b'sRGB', b'gAMA', b'pHYs', b'cHRM'):
            raise ValueError('unexpected screenshot metadata')
        if kind==b'IEND' and offset==len(raw):
            return
    raise ValueError('invalid screenshot chunks')


def inspect(path, raw):
    if path not in FILES:
        raise ValueError('outside the release allowlist')
    if path=='docs/dashboard.png':
        check_image(raw)
        return
    if len(raw)>160_000 or b'\0' in raw:
        raise ValueError('unexpected binary or large file')
    text = raw.decode('utf-8-sig')
    for label, pattern in CHECKS.items():
        if re.search(pattern,text):
            raise ValueError(label)
    if any(not item.lower().endswith('@example.invalid') for item in EMAIL.findall(text)):
        raise ValueError('email outside the reserved demo domain')
    if any(item not in ('+1-202-555-0100','+1-202-555-0101') for item in PHONE.findall(text)):
        raise ValueError('non-demo phone pattern')
    if path.startswith(('app/','tests/')) and any(host not in ('127.0.0.1','example.invalid') for host in URL.findall(text)):
        raise ValueError('external URL in executable demo sources')


def verify_records(entries, staged, scanned):
    for entry in entries.decode().strip('\0').split('\0'):
        if not entry:
            continue
        header, path = entry.split('\t',1)
        mode, second, third = header.split()
        if mode not in ('100644','100755') or (staged and third!='0'):
            raise ValueError(f'{path}: symlink, submodule or unresolved index')
        blob = second if staged else third
        if (path,blob) not in scanned:
            try:
                inspect(path,git('cat-file','blob',blob))
            except ValueError as error:
                raise ValueError(f'{path}: {error}') from None
            scanned.add((path,blob))


def main():
    scanned = set()
    verify_records(git('ls-files','--stage','-z'), True, scanned)
    history = git('rev-list','--all').decode().splitlines()
    for revision in history:
        verify_records(git('ls-tree','-r','-z',revision), False, scanned)
        metadata = git('show','-s','--format=%an%n%ae%n%cn%n%ce%n%B',revision).decode()
        if any(not address.endswith('@example.invalid') for address in EMAIL.findall(metadata)):
            raise ValueError('non-demo commit identity')
        if history and any(re.search(pattern,metadata) for pattern in CHECKS.values()):
            raise ValueError('sensitive pattern in commit metadata')
    if not scanned:
        raise ValueError('empty index and history')
    print(f'PASS: {len(scanned)} approved path/object pairs; {len(history)} reachable commits')


if __name__=='__main__':
    try:
        main()
    except (subprocess.CalledProcessError, ValueError) as error:
        sys.exit(str(error))
