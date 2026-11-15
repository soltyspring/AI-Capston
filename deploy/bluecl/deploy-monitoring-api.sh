#!/bin/sh
set -eu
umask 077

base=/home/solty/.local/share/aicap
source_file=${1:?staged backend/api.py path is required}
release_id=${2:?release id is required}
case "$release_id" in
    *[!A-Za-z0-9._-]*|'') echo 'invalid release id' >&2; exit 2 ;;
esac
test -s "$source_file"
previous=$(readlink -f "$base/current")
release="$base/releases/$release_id"
test -d "$previous"
test ! -e "$release"
test ! -e "$base/current.next"
mkdir -p "$release"
cp -a "$previous/." "$release/"
install -m 644 "$source_file" "$release/backend/api.py"
"$base/venv/bin/python" -m py_compile "$release/backend/api.py"

switched=0
on_exit() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ] && [ "$switched" -eq 1 ]; then
        rollback_link="$base/current.rollback.$release_id"
        ln -s "$previous" "$rollback_link"
        mv -Tf "$rollback_link" "$base/current"
        systemctl --user restart aicap.service || true
    fi
    exit "$result"
}
trap on_exit EXIT

ln -s "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
switched=1
systemctl --user restart aicap.service
healthy=0
for attempt in $(seq 1 40); do
    if curl -fsS --max-time 2 http://127.0.0.1:18822/api/state -o /dev/null 2>/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done
test "$healthy" -eq 1
curl -fsS --max-time 30 -H 'Content-Type: application/json' \
    --data '{"action":"monitor_dashboard","hours":24,"limit":2}' \
    http://127.0.0.1:18822/api/control |
    "$base/venv/bin/python" -c 'import json,sys; payload=json.load(sys.stdin); analysis=payload.get("analysis"); assert isinstance(analysis,dict); assert {"summary","replay","benchmarks"}.issubset(analysis); assert payload.get("sample_interval_seconds")==3'
test "$(systemctl --user is-active aicap.service)" = active
echo "deployed $release_id; monitor_dashboard includes cached analysis"
