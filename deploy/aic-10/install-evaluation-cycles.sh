#!/bin/bash
set -euo pipefail
umask 077
workspace="$HOME/.local/share/qless-training"
staging="$workspace/cycle-release"
router="$HOME/round"
cd "$staging"
python3 enable-evaluation-cycles.py "$router" "$staging"
backup=$(mktemp -d "$workspace/cycle-backup.XXXXXX")
cp -p "$router/dispatcher.py" "$router/vllm_balancer.py" "$workspace/continuous_worker.py" "$backup/"
old_memory=$(systemctl show -p MemoryMax --value qless-training.service)
restore() {
    code=$?
    if ((code)); then
        cp -p "$backup/dispatcher.py" "$backup/vllm_balancer.py" "$router/"
        cp -p "$backup/continuous_worker.py" "$workspace/"
        sudo -n systemctl set-property qless-training.service "MemoryMax=$old_memory" || true
        sudo -n systemctl restart round-balancer.service || true
    fi
    sudo -n systemctl start qless-training.service
}
sudo -n systemctl stop qless-training.service
trap restore EXIT
for attempt in $(seq 1 30); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/status",timeout=3)); sys.exit(bool(any(d["inflight"].values()) or d["queue"]["waiting"]))'; then
        break
    fi
    sleep 2
done
if [[ "$attempt" == 30 ]]; then exit 1; fi
for file in dispatcher.py vllm_balancer.py qless_cycle_router.py length_forecast.py; do
    install -m 600 "$file" "$router/$file.next"
    mv "$router/$file.next" "$router/$file"
done
sudo -n systemctl restart round-balancer.service
for attempt in $(seq 1 20); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/status",timeout=3)); sys.exit("evaluation_cycles" not in d)'; then
        break
    fi
    sleep 2
done
if [[ "$attempt" == 20 ]]; then exit 1; fi
sudo -n systemctl set-property qless-training.service MemoryMax=640M
for file in continuous_worker.py evaluation_cycles.py length_forecast.py train_length_forecast.py; do
    install -m 600 "$file" "$workspace/$file.next"
    mv "$workspace/$file.next" "$workspace/$file"
done
printf 'Evaluation cycles installed; previous sources: %s\n' "$backup"
