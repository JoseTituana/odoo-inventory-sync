#!/usr/bin/env bash
# Restore a backup bundle into a SEPARATE database and compare it with the
# live one. Never touches the live database.
#
# Usage: restore-test.sh /opt/backups/backup-YYYY-MM-DD_HHMM.tar.gz [target_db]
# The target database must not exist yet (default: jats_staging).
set -euo pipefail

BUNDLE="${1:?usage: restore-test.sh BUNDLE [target_db]}"
TARGET="${2:-jats_staging}"
LIVE_DB="${LIVE_DB:-jats}"
DB_USER="${DB_USER:-odoo}"
DB_CONTAINER="${DB_CONTAINER:-odoo-db-1}"
ODOO_DATA="${ODOO_DATA:-/var/lib/docker/volumes/odoo_odoo-data/_data}"

log() { echo "$(date '+%F %T') $*"; }
fail() { log "ERROR: $*"; exit 1; }
psql_at() { docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$1" -Atc "$2"; }

[ "$TARGET" != "$LIVE_DB" ] || fail "refusing to restore over the live database"
exists=$(psql_at postgres "select 1 from pg_database where datname = '$TARGET'")
[ -z "$exists" ] || fail "database $TARGET already exists; drop it yourself first"

log "checking checksum"
(cd "$(dirname "$BUNDLE")" && sha256sum -c "$(basename "$BUNDLE").sha256")

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -C "$work" -xzf "$BUNDLE"

log "restoring database into $TARGET"
docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$TARGET"
docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$TARGET" \
    --no-owner --exit-on-error < "$work/odoo-db.dump"

if [ -f "$work/odoo-filestore.tar" ]; then
    log "restoring filestore into filestore/$TARGET"
    owner=$(stat -c '%u:%g' "$ODOO_DATA/filestore")
    mkdir "$work/fs"
    tar -C "$work/fs" -xf "$work/odoo-filestore.tar"
    mv "$work/fs/$LIVE_DB" "$ODOO_DATA/filestore/$TARGET"
    chown -R "$owner" "$ODOO_DATA/filestore/$TARGET"
fi

log "comparing row counts (live may be slightly ahead if it changed since the backup)"
status=0
for table in res_users res_partner product_template product_product \
             stock_quant stock_move ir_attachment ir_module_module; do
    live=$(psql_at "$LIVE_DB" "select count(*) from $table" 2>/dev/null || echo "-")
    restored=$(psql_at "$TARGET" "select count(*) from $table" 2>/dev/null || echo "-")
    # "-" means the table does not exist (its Odoo app is not installed).
    if [ "$live" = "-" ] && [ "$restored" = "-" ]; then
        mark="not installed"
    elif [ "$restored" = "-" ]; then
        mark="MISSING"; status=1
    elif [ "$live" = "$restored" ]; then
        mark="ok"
    else
        mark="DIFF"
    fi
    printf '  %-20s live=%-6s restored=%-6s %s\n' "$table" "$live" "$restored" "$mark"
done

if [ -f "$work/n8n/database.sqlite" ]; then
    log "checking n8n copy"
    python3 - "$work/n8n/database.sqlite" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
print("  integrity:", db.execute("PRAGMA integrity_check").fetchone()[0])
for table in ("workflow_entity", "credentials_entity"):
    try:
        print(f"  {table}: {db.execute(f'select count(*) from {table}').fetchone()[0]}")
    except sqlite3.Error as exc:
        print(f"  {table}: {exc}")
PY
fi

[ "$status" -eq 0 ] && log "restore test PASSED" || fail "restore test FAILED"
