#!/bin/bash
# Install experiment-only v4 router policies; restore the previous router on any failure.
set -euo pipefail
umask 077
router="$HOME/round"
stage=$(mktemp -d "$HOME/.local/share/qless-training/v4-stage.XXXXXX")
backup=$(mktemp -d "$HOME/.local/share/qless-training/v4-backup.XXXXXX")
cp -p "$router/dispatcher.py" "$router/vllm_balancer.py" "$backup/"
python3 "$HOME/.local/share/qless-training/enable-v4-policy.py" "$router" "$stage"
restore() {
    code=$?
    if ((code)); then
        cp -p "$backup/dispatcher.py" "$backup/vllm_balancer.py" "$router/"
        sudo -n systemctl restart round-balancer.service || true
        echo "restored previous router from $backup" >&2
    fi
}
trap restore EXIT
for attempt in $(seq 1 60); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/status",timeout=3)); sys.exit(bool(any(d["inflight"].values()) or d["queue"]["waiting"]))'; then
        break
    fi
    sleep 2
done
[[ "$attempt" != 60 ]]
install -m 600 "$stage/dispatcher.py" "$router/dispatcher.py"
install -m 600 "$stage/vllm_balancer.py" "$router/vllm_balancer.py"
sudo -n systemctl restart round-balancer.service
for attempt in $(seq 1 30); do
    if python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8008/status",timeout=3)); sys.exit(not (d.get("v4_policies") and sum(bool(s.get("healthy")) for s in d["servers"].values())==3))'; then
        break
    fi
    sleep 2
done
[[ "$attempt" != 30 ]]
printf 'v4 policies installed; previous router: %s\n' "$backup"
