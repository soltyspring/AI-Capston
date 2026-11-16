import json
import sys
from pathlib import Path
key = sys.argv[1] if len(sys.argv) > 1 else 'v4_trials'
d = json.loads((Path.home()/'.local/share/qless-training/shadow-comparison.json').read_text()).get(key) or {}
print(d.get('status'), d.get('current'), d.get('reason'))
for b in d.get('burst', []):
    print('burst', b['repeat'], b['policy'], b['short'], 'long p95', b['long']['completion_p95'], b['queued_at_short'])
for s in d.get('stream', []):
    print('stream', s['repeat'], s['policy'], {k: s[k] for k in ('first_p95', 'over_2s_pct', 'completion_mean', 'completion_p95', 'wait_p95', 'makespan')})
print('length', d.get('length'))
