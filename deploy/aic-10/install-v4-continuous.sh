#!/bin/bash
# Add the v4 policies to the continuous experiment and start periodic retraining.
# Production routing (linucb) is unchanged. Restores the previous worker on failure.
set -euo pipefail
umask 077
workspace="$HOME/.local/share/qless-training"
stage="$workspace/v4-continuous-stage"
backup=$(mktemp -d "$workspace/v4-continuous-backup.XXXXXX")
cp -p "$workspace/continuous_worker.py" "$workspace/evaluation_cycles.py" "$backup/"
restore() {
    code=$?
    if ((code)); then
        cp -p "$backup/continuous_worker.py" "$backup/evaluation_cycles.py" "$workspace/"
        echo "restored previous worker from $backup" >&2
    fi
    sudo -n systemctl start qless-training.service
}
cd "$stage"
python3 -m py_compile continuous_worker.py evaluation_cycles.py retrain_v4.py
printf '{"version": "v4.1", "trained_until": 18512, "rows": 18512, "updated_at": %s}\n' "$(date +%s)" > "$workspace/models-v4/state.json.new"
[[ -f "$workspace/models-v4/state.json" ]] || mv "$workspace/models-v4/state.json.new" "$workspace/models-v4/state.json"
rm -f "$workspace/models-v4/state.json.new"
sudo -n systemctl stop qless-training.service
trap restore EXIT
for file in continuous_worker.py evaluation_cycles.py retrain_v4.py; do
    install -m 600 "$file" "$workspace/$file"
done
sudo -n install -m 644 qless-v4-retrain.service qless-v4-retrain.timer /etc/systemd/system/
sudo -n systemctl daemon-reload
# The worker now loads nothing extra; the router loads v4 on demand.
sudo -n systemctl enable --now qless-v4-retrain.timer
printf 'v4 continuous experiment installed; previous worker: %s\n' "$backup"
