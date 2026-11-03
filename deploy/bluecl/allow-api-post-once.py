#!/usr/bin/env python3
"""Allow only AICAP's two POST endpoints; preserve all other nginx rules."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

if os.geteuid() != 0:
    raise SystemExit('Run with sudo python3.')
target = Path('/etc/nginx/conf.d/bluecl-security-http.conf')
if target.is_symlink() or not target.is_file():
    raise SystemExit('Expected regular method-map file not found.')
original = target.read_text()
anchor = '    "POST:/_bluecl-analytics/collect" 1;'
if original.count(anchor) != 1 or 'map "$request_method:$uri" $bluecl_method_ok {' not in original:
    raise SystemExit('Unexpected method-map structure; refusing changes.')
rules = ['    "POST:/aicap/api/control" 1;', '    "POST:/aicap/api/step" 1;']
missing = [rule for rule in rules if rule not in original]
subprocess.run(['nginx', '-t'], check=True)
backup = Path(tempfile.mkdtemp(prefix='aicap-api-methods-', dir='/var/backups'))
shutil.copy2(target, backup / target.name)
try:
    if missing:
        target.write_text(original.replace(anchor, '\n'.join(missing) + '\n' + anchor))
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
except Exception:
    shutil.copy2(backup / target.name, target)
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    raise
print(f'AICAP POST endpoints allowed. Backup: {backup}')
print('Only /aicap/api/control and /aicap/api/step were added; other rules unchanged.')
