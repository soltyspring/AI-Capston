#!/bin/sh
set -eu
umask 077
mode=${1:?collector or central required}
case "$mode" in collector|central) ;; *) exit 2 ;; esac
root=/home/ubuntu/.local/share/qless-training
agent_dir=$(systemctl show round-agent.service -p WorkingDirectory --value)
case "$agent_dir" in /home/ubuntu/round|/home/ubuntu/round-agent) ;; *) exit 2 ;; esac
agent="$agent_dir/agent.py"
unit=/etc/systemd/system/qless-training.service
backup=$(mktemp "$root/agent.before.XXXXXX")
cp -p "$agent" "$backup"
python3 -m py_compile "$root/quiet_metrics_agent.py"
if [ "$mode" = central ]; then
    python3 -m py_compile "$root/continuous_worker.py"
    python3 - "$root" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1]) / 'workload'
with (root / 'requests.jsonl').open('rb') as requests:
    digest = hashlib.file_digest(requests, 'sha256').hexdigest()
assert digest == json.loads((root / 'manifest.json').read_text())['sha256']
PY
fi
trap 'result=$?; if [ "$result" -ne 0 ]; then
  if [ "$mode" = central ]; then sudo -n systemctl disable --now qless-training.service || true; fi
  install -m 644 "$backup" "$agent"
  sudo -n systemctl restart round-agent.service || true
fi' EXIT
if ! cmp -s "$root/quiet_metrics_agent.py" "$agent"; then
    install -m 644 "$root/quiet_metrics_agent.py" "$agent"
    sudo -n systemctl restart round-agent.service
fi
for attempt in 1 2 3 4 5; do
    if curl -fsS --max-time 2 http://127.0.0.1:9109/metrics 2>/dev/null > "$root/first-metrics.json"; then
        break
    fi
    sleep 1
done
python3 - "$root/first-metrics.json" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    row = json.load(source)
assert row['gpus'] and row['gpu_source'] in ('nvml', 'nvidia-smi')
print('collector:', row['gpu_source'])
PY
if [ "$mode" = central ]; then
    sudo -n install -m 644 "$root/qless-training.service" "$unit"
    sudo -n systemctl daemon-reload
    sudo -n systemctl enable --now qless-training.service
    systemctl is-active qless-training.service
fi
