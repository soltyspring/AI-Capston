#!/bin/sh
# One-time, user-invoked root installation. No new sudo rules or exposed ports.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'Run this reviewed script with sudo.' >&2
    exit 1
fi
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG=/etc/nginx/sites-available/bluecl.cloud
SNIPPET=/etc/nginx/snippets/aicap.conf
INCLUDE='    include /etc/nginx/snippets/aicap.conf;'
if [ ! -f "$CONFIG" ] || [ -L "$CONFIG" ] || [ ! -f "$SCRIPT_DIR/aicap.conf" ]; then
    printf '%s\n' 'Expected regular nginx configuration files were not found.' >&2
    exit 1
fi
if ! grep -Fq 'root /var/www/bluecl;' "$CONFIG"; then
    printf '%s\n' 'Unexpected vhost root; refusing to change nginx.' >&2
    exit 1
fi
if [ -L "$SNIPPET" ]; then
    printf '%s\n' 'Refusing to replace a symlink snippet.' >&2
    exit 1
fi
if [ -f "$SNIPPET" ] && ! cmp -s "$SCRIPT_DIR/aicap.conf" "$SNIPPET"; then
    printf '%s\n' 'A different aicap.conf already exists; review it manually.' >&2
    exit 1
fi
nginx -t
BACKUP=$(mktemp -d /var/backups/aicap-nginx-XXXXXXXX)
cp -p "$CONFIG" "$BACKUP/bluecl.cloud"
if [ -f "$SNIPPET" ]; then
    cp -p "$SNIPPET" "$BACKUP/aicap.conf"
fi
restore() {
    cp -p "$BACKUP/bluecl.cloud" "$CONFIG"
    if [ -f "$BACKUP/aicap.conf" ]; then
        cp -p "$BACKUP/aicap.conf" "$SNIPPET"
    else
        rm -f /etc/nginx/snippets/aicap.conf
    fi
}
trap 'restore' HUP INT TERM
if ! grep -Fq 'include /etc/nginx/snippets/aicap.conf;' "$CONFIG"; then
    awk -v aicap_include_line="$INCLUDE" '
      { print }
      /root \/var\/www\/bluecl;/ && !added { print aicap_include_line; added=1 }
    ' "$CONFIG" > "$BACKUP/new-vhost"
    if ! install -o root -g root -m 0644 "$BACKUP/new-vhost" "$CONFIG"; then
        restore
        exit 1
    fi
fi
if ! install -o root -g root -m 0644 "$SCRIPT_DIR/aicap.conf" "$SNIPPET"; then
    restore
    exit 1
fi
if ! nginx -t; then
    restore
    printf '%s\n' "Validation failed; original config restored. Backup: $BACKUP" >&2
    exit 1
fi
if ! systemctl reload nginx; then
    restore
    nginx -t && systemctl reload nginx
    printf '%s\n' "Reload failed; original config restored. Backup: $BACKUP" >&2
    exit 1
fi
trap - HUP INT TERM
printf '%s\n' "AICAP proxy installed. Backup: $BACKUP"
printf '%s\n' 'Existing DNS, HTTPS certificates, firewall and sudo permissions were not changed.'
