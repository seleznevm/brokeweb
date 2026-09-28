#!/bin/sh
set -eu
umask 077
cd /docker/brokeweb
mkdir -p /var/backups/brokeweb
exec 9>/var/backups/brokeweb/backup.lock
flock -n 9 || exit 0
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target=/var/backups/brokeweb/$stamp.dump
trap 'rm -f "$target.partial"' EXIT
docker compose --env-file .env.vps -f compose.yaml -f compose.vps.yaml exec -T postgres pg_dump -U brokeweb -d brokeweb -Fc > "$target.partial"
docker compose --env-file .env.vps -f compose.yaml -f compose.vps.yaml exec -T postgres pg_restore --list < "$target.partial" > /dev/null
mv "$target.partial" "$target"
sha256sum "$target" > "$target.sha256"
# Delete only this job's dated backups older than seven days.
find /var/backups/brokeweb -maxdepth 1 -type f -name '20*T*Z.dump*' -mtime +7 -delete
