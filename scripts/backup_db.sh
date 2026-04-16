#!/usr/bin/env bash
# HedgeSpark automated DB backup — runs every 6h via cron.
# Compressed pg_dump with 14-day rotation.
#
# Restore: gunzip < backup.sql.gz | psql wishspark
set -euo pipefail

BACKUP_DIR="/opt/wishspark/backups"
DB_NAME="wishspark"
export PGHOST="localhost"
export PGPORT="5432"
export PGUSER="aiuser"
export PGPASSWORD="aipassword"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/wishspark_${TIMESTAMP}.sql.gz"

# Dump + compress in one pipe (no intermediate uncompressed file)
pg_dump "$DB_NAME" --no-owner --no-acl | gzip > "$BACKUP_FILE"

# Verify the backup is non-trivial (> 10KB)
SIZE=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 10240 ]; then
    echo "BACKUP FAILED: ${BACKUP_FILE} is only ${SIZE} bytes" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Rotate: delete backups older than RETENTION_DAYS
find "$BACKUP_DIR" -name "wishspark_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

# Log
COUNT=$(find "$BACKUP_DIR" -name "wishspark_*.sql.gz" | wc -l)
echo "OK: ${BACKUP_FILE} ($(numfmt --to=iec ${SIZE})) — ${COUNT} backups retained"
