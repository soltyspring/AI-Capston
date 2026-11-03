#!/usr/bin/env python3
"""Restore only the two AICAP POST exceptions and preserve them on refresh."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MAP = Path('/etc/nginx/conf.d/bluecl-security-http.conf')
GENERATOR = Path('/usr/local/sbin/bluecl-security')
ANCHOR = '    "POST:/_bluecl-analytics/collect" 1;'
RULES = ('    "POST:/aicap/api/control" 1;', '    "POST:/aicap/api/step" 1;')


def patched(text):
    if text.count(ANCHOR) != 1 or 'map "$request_method:$uri" $bluecl_method_ok {' not in text:
        raise ValueError('Unexpected method map; refusing changes')
    missing = [rule for rule in RULES if rule not in text]
    return text.replace(ANCHOR, '\n'.join(missing) + '\n' + ANCHOR, 1) if missing else text


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this script with sudo python3.')
    for path in (MAP, GENERATOR):
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f'Expected regular file: {path}')
    original = {path: path.read_text() for path in (MAP, GENERATOR)}
    if "HTTP_RULES = r'''" not in original[GENERATOR]:
        raise SystemExit('Unexpected generator structure; refusing changes')
    updated = {path: patched(text) for path, text in original.items()}
    compile(updated[GENERATOR], str(GENERATOR), 'exec')
    subprocess.run(['nginx', '-t'], check=True)
    backup = Path(tempfile.mkdtemp(prefix='aicap-method-repair-', dir='/var/backups'))
    for path in original:
        shutil.copy2(path, backup / path.name)
    try:
        for path, text in updated.items():
            path.write_text(text)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        for path in original:
            shutil.copy2(backup / path.name, path)
        subprocess.run(['nginx', '-t'], check=False)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=False)
        raise
    print(f'AICAP POST exceptions restored; generator preserved. Backup: {backup}')
    print('Allowed only /aicap/api/control and /aicap/api/step; all other rules unchanged.')


if __name__ == '__main__':
    main()
