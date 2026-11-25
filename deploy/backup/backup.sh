#!/usr/bin/env bash
# Nightly backup of Odoo (database + filestore) and n8n data.
#
# Writes one bundle per run to $BACKUP_DIR:
#   backup-YYYY-MM-DD_HHMM.tar.gz         the data
#   backup-YYYY-MM-DD_HHMM.tar.gz.sha256  checksum, written last
# A bundle without its .sha256 file is incomplete and must be ignored.
#
# Secrets (/opt/odoo/.env, config/odoo.conf) are deliberately NOT included:
# keep those passwords in the password manager.
#
# Run as root, normally from /etc/cron.d/odoo-backup.
set -euo pipefail
umask 027

BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
DB_NAME="${DB_NAME:-jats}"
DB_USER="${DB_USER:-odoo}"
DB_CONTAINER="${DB_CONTAINER:-odoo-db-1}"
ODOO_DATA="${ODOO_DATA:-/var/lib/docker/volumes/odoo_odoo-data/_data}"
N8N_DATA="${N8N_DATA:-/var/lib/docker/volumes/n8n_n8n_data/_data}"
# Group allowed to read finished bundles (the read-only pull user).
READ_GROUP="${READ_GROUP:-bkpull}"
MIN_FREE_MB="${MIN_FREE_MB:-1024}"

log() { echo "$(date '+%F %T') $*"; }
fail() { log "ERROR: $*"; exit 1; }

# Only one backup at a time.
exec 9>/run/lock/odoo-backup.lock
flock -n 9 || fail "another backup is already running"

mkdir -p "$BACKUP_DIR"
free_mb=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
[ "$free_mb" -ge "$MIN_FREE_MB" ] || fail "only ${free_mb} MB free in $BACKUP_DIR"

stamp=$(date '+%F_%H%M')
name="backup-$stamp"
work=$(mktemp -d "$BACKUP_DIR/.work-XXXXXX")
trap 'rm -rf "$work" "$BACKUP_DIR/$name.tar.gz.partial"' EXIT

log "starting $name"

# 1. Odoo database. pg_dump takes a consistent snapshot while Odoo keeps running.
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$work/odoo-db.dump"
docker exec -i "$DB_CONTAINER" pg_restore --list < "$work/odoo-db.dump" > /dev/null \
    || fail "database dump is not readable"
log "database dumped ($(du -h "$work/odoo-db.dump" | cut -f1))"

# 2. Odoo filestore (product images, attachments).
if [ -d "$ODOO_DATA/filestore/$DB_NAME" ]; then
    tar -C "$ODOO_DATA/filestore" -cf "$work/odoo-filestore.tar" "$DB_NAME"
    log "filestore archived"
else
    log "WARNING: no filestore found for $DB_NAME"
fi

# 3. n8n. Copy the SQLite database with the online backup API so a running
#    n8n cannot leave it half-written, then copy the other files as they are.
if [ -d "$N8N_DATA" ]; then
    mkdir "$work/n8n"
    python3 - "$N8N_DATA/database.sqlite" "$work/n8n/database.sqlite" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
src.close()
sys.exit(0 if ok == "ok" else f"n8n integrity check failed: {ok}")
PY
    tar -C "$N8N_DATA" \
        --exclude='./database.sqlite' --exclude='./database.sqlite-*' \
        --exclude='./crash.journal' \
        -cf "$work/n8n/files.tar" .
    log "n8n copied"
else
    log "WARNING: no n8n data found"
fi

# 4. Bundle, verify, checksum. The rename and the .sha256 file come last,
#    so a reader never sees a half-written bundle as complete.
tar -C "$work" -czf "$BACKUP_DIR/$name.tar.gz.partial" .
gzip -t "$BACKUP_DIR/$name.tar.gz.partial" || fail "bundle failed gzip test"
mv "$BACKUP_DIR/$name.tar.gz.partial" "$BACKUP_DIR/$name.tar.gz"
(cd "$BACKUP_DIR" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256.partial" \
    && mv "$name.tar.gz.sha256.partial" "$name.tar.gz.sha256")
if getent group "$READ_GROUP" > /dev/null; then
    chgrp "$READ_GROUP" "$BACKUP_DIR/$name.tar.gz" "$BACKUP_DIR/$name.tar.gz.sha256"
fi
chmod 640 "$BACKUP_DIR/$name.tar.gz" "$BACKUP_DIR/$name.tar.gz.sha256"
log "wrote $name.tar.gz ($(du -h "$BACKUP_DIR/$name.tar.gz" | cut -f1))"

# 5. Retention: delete only our own bundles older than KEEP_DAYS.
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'backup-*.tar.gz' -o -name 'backup-*.tar.gz.sha256' \) \
    -mtime +"$((KEEP_DAYS - 1))" -print -delete | sed 's/^/deleted /'

log "done"
