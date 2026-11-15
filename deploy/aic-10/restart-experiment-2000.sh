#!/bin/bash
set -euo pipefail
umask 077
workspace="$HOME/.local/share/qless-training"
router="$HOME/round"
stage=$(cd -- "$(dirname -- "$0")" && pwd)
test "$stage" = "$workspace/restart-2000-staging"
cd "$stage"
sudo -n systemctl stop qless-training.service
python3 enable-evaluation-cycles.py "$router" "$stage"
if [[ ! -e "$stage/workload/manifest.json" ]]; then
    python3 prepare-training-workload.py "$workspace/workload/requests.jsonl" "$stage/workload" --max-tokens 2000
fi
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('workload')
m = json.loads((root/'manifest.json').read_text())
with (root/'requests.jsonl').open('rb') as rows:
    assert hashlib.file_digest(rows,'sha256').hexdigest() == m['sha256']
assert m['requests'] == 100000 and m['decode_budgets'] == {'2000':100000}
PY
python3 -m py_compile vllm_balancer.py dispatcher.py continuous_worker.py train_length_forecast.py evaluation_cycles.py
archive=$(mktemp -d "$workspace/run-before-2000.XXXXXX")
mkdir "$archive/experiment" "$archive/router" "$archive/worker"
cp -p "$router/vllm_balancer.py" "$archive/router/"
for file in continuous_worker.py train_length_forecast.py evaluation_cycles.py; do
    cp -p "$workspace/$file" "$archive/worker/"
done
switched=0
rollback() {
    code=$?
    if ((code != 0 && switched == 1)); then
        sudo -n systemctl stop qless-training.service round-balancer.service || true
        if [[ -e "$archive/experiment/workload" ]]; then
            mkdir "$archive/failed-run"
            for item in "$workspace"/{workload,cycles,central-training.sqlite3*,shadow-linucb.sqlite3*}; do
                [[ ! -e "$item" ]] || mv -- "$item" "$archive/failed-run/"
            done
            for item in "$archive/experiment/"*; do mv -- "$item" "$workspace/"; done
        fi
        cp -p "$archive/router/vllm_balancer.py" "$router/"
        cp -p "$archive/worker/"*.py "$workspace/"
        sudo -n systemctl start round-balancer.service || true
    fi
}
trap rollback EXIT
idle=0
for i in $(seq 1 25); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/status",timeout=3)); sys.exit(bool(any(d["inflight"].values()) or d["queue"]["waiting"]))' 2>/dev/null; then
        idle=1
        break
    fi
    sleep 2
done
((idle == 1))
sudo -n systemctl stop round-balancer.service
switched=1
for item in "$workspace"/{cycles,central-training.sqlite3*,shadow-linucb.sqlite3*,shadow-comparison*.json,output-length-*.json,workload}; do
    [[ ! -e "$item" ]] || mv -- "$item" "$archive/experiment/"
done
mv -- "$stage/workload" "$workspace/workload"
install -m 600 vllm_balancer.py "$router/vllm_balancer.py"
for file in continuous_worker.py train_length_forecast.py evaluation_cycles.py; do
    install -m 600 "$file" "$workspace/$file"
done
sudo -n systemctl start round-balancer.service
ready=0
for i in $(seq 1 20); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/openapi.json",timeout=3)); sys.exit(d["components"]["schemas"]["ChatRequest"]["properties"]["training_max_tokens"]["maximum"] != 2000)' 2>/dev/null; then
        ready=1
        break
    fi
    sleep 2
done
((ready == 1))
sudo -n systemctl start qless-training.service
printf 'Fresh 100000-request experiment started; previous run archived in %s\n' "$archive"
