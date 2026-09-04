#!/bin/bash
# Automated recreo-db backup (SECURITY_AUDIT.md HIGH-3).
#
# Runs OUTSIDE any container, on the EC2 host, via cron (see
# deploy/systemd or crontab — installed manually, see ARCHITECTURE.md).
# Writes to /home/ubuntu/backups/recreo-bienestar/ — deliberately OUTSIDE
# the recreo-bienestar-backend/ directory this repo syncs to, so a dump
# (which contains real user data and password hashes) can never
# accidentally be swept up by an rsync or git operation targeting that
# directory.
#
# Retention: keeps the last RETENTION_DAYS days of daily dumps, deletes
# anything older on every run.
#
# Restore procedure (also in ARCHITECTURE.md §14):
#   docker cp /home/ubuntu/backups/recreo-bienestar/<file>.dump recreo-db:/tmp/restore.dump
#   docker exec recreo-db pg_restore -U recreo_admin -d recreo_bienestar \
#     --clean --if-exists /tmp/restore.dump
#   docker exec recreo-db rm -f /tmp/restore.dump   # clean up the copy inside the container

set -euo pipefail

BACKUP_DIR="/home/ubuntu/backups/recreo-bienestar"
RETENTION_DAYS=14
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/recreo_bienestar_${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"

docker exec recreo-db pg_dump -U recreo_admin -d recreo_bienestar --format=custom > "$DUMP_FILE"

# Sanity check: a valid custom-format dump must be listable. Catches a
# truncated/corrupt dump (e.g. disk full mid-write) before it's trusted as
# a real backup — an empty or broken file left behind gets removed rather
# than silently kept as if it were a working backup.
if docker exec -i recreo-db pg_restore --list < "$DUMP_FILE" > /dev/null 2>&1; then
  echo "$(date -Is) OK: ${DUMP_FILE} ($(du -h "$DUMP_FILE" | cut -f1))"
else
  echo "$(date -Is) ERROR: ${DUMP_FILE} failed pg_restore --list validation, removing" >&2
  rm -f "$DUMP_FILE"
  exit 1
fi

find "$BACKUP_DIR" -name 'recreo_bienestar_*.dump' -mtime "+${RETENTION_DAYS}" -print -delete
