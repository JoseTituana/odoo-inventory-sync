#!/usr/bin/env bash
# Create or refresh the staging database jats_staging from the newest backup
# bundle, for the separate odoo-staging service (see compose.yaml).
#
# Usage (as root on the server, from /opt/odoo):  setup-staging.sh --yes
# --yes is required because jats_staging and its files are DELETED and rebuilt.
# The live database jats is never modified (only its CONNECT right is limited
# to its owner, so the staging database user cannot open it).
#
# The staging database user and master passwords are random, generated here,
# and written only to config-staging/odoo.conf.
[ "${1:-}" = "--yes" ] || { echo "usage: $0 --yes   (deletes and rebuilds jats_staging)" >&2; exit 1; }
set -euo pipefail
cd /opt/odoo
docker compose stop odoo-staging 2>/dev/null || true
[ -f compose.yaml.before-staging ] || cp -a compose.yaml compose.yaml.before-staging
PG="docker exec -i odoo-db-1 psql -U odoo -v ON_ERROR_STOP=1 -q"
OWNER=$(stat -c '%u:%g' /var/lib/docker/volumes/odoo_odoo-data/_data/filestore)
echo "odoo uid:gid in container = $OWNER"

# Secrets are generated here and only written into the staging config.
DBPASS=$(openssl rand -hex 24)
MASTER=$(openssl rand -hex 24)

# 1. Role that can only own and use jats_staging (password via stdin, not argv).
if [ -z "$(echo "select 1 from pg_roles where rolname = 'odoo_staging';" | $PG -d postgres -At)" ]; then
    echo "CREATE ROLE odoo_staging LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;" | $PG -d postgres
fi
printf "ALTER ROLE odoo_staging PASSWORD '%s';\n" "$DBPASS" | $PG -d postgres
# Nobody except the owner/superuser may connect to the live database.
echo "REVOKE CONNECT ON DATABASE jats FROM PUBLIC;" | $PG -d postgres

# 2. Fresh jats_staging from the latest backup, owned by odoo_staging.
docker exec odoo-db-1 dropdb -U odoo --if-exists jats_staging
rm -rf /var/lib/docker/volumes/odoo_odoo-data/_data/filestore/jats_staging /opt/odoo/staging-data/filestore/jats_staging
docker exec odoo-db-1 createdb -U odoo -O odoo_staging jats_staging
echo "REVOKE CONNECT ON DATABASE jats_staging FROM PUBLIC; GRANT CONNECT ON DATABASE jats_staging TO odoo_staging;" | $PG -d postgres
BUNDLE=$(ls -1 /opt/backups/backup-*.tar.gz | sort | tail -1)
echo "restoring from $BUNDLE"
(cd /opt/backups && sha256sum -c "$(basename "$BUNDLE").sha256")
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
tar -C "$work" -xzf "$BUNDLE"
docker exec -i odoo-db-1 pg_restore -U odoo -d jats_staging --no-owner --role=odoo_staging --exit-on-error < "$work/odoo-db.dump"

# 3. Staging data dir (bind mount) with its own copy of the filestore.
install -d -o "${OWNER%:*}" -g "${OWNER#*:}" -m 750 staging-data staging-data/filestore
tar -C staging-data/filestore -xf "$work/odoo-filestore.tar"
mv staging-data/filestore/jats staging-data/filestore/jats_staging
chown -R "$OWNER" staging-data

# 4. Staging config.
install -d -o root -g root -m 755 config-staging
umask 027
cat > config-staging/odoo.conf <<EOF
; Staging Odoo: reachable only on 127.0.0.1:8070 (SSH tunnel).
[options]
admin_passwd = $MASTER
db_host = db
db_port = 5432
db_user = odoo_staging
db_password = $DBPASS
db_name = jats_staging
db_maxconn = 8
dbfilter = ^jats_staging\$
list_db = False
addons_path = /mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons
data_dir = /var/lib/odoo
proxy_mode = False
workers = 0
limit_time_cpu = 120
limit_time_real = 240
; Staging must never send real email: nothing listens on this port.
smtp_server = localhost
smtp_port = 2525
EOF
chown "$OWNER" config-staging/odoo.conf
chmod 640 config-staging/odoo.conf

echo "== checks"
echo "objects not owned by odoo_staging in jats_staging:"
echo "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and pg_get_userbyid(c.relowner) <> 'odoo_staging';" | $PG -d jats_staging -At
echo "mail servers in staging:"; echo "select count(*) from ir_mail_server;" | $PG -d jats_staging -At
echo "staging role can reach jats? (expect permission denied):"
printf '%s\n' "$DBPASS" > /dev/null
PGPASSWORD="$DBPASS" docker exec -e PGPASSWORD odoo-db-1 psql -h 127.0.0.1 -U odoo_staging -d jats -Atc "select 1" 2>&1 | tail -1 || true
echo "staging role can reach jats_staging? (expect 1):"
PGPASSWORD="$DBPASS" docker exec -e PGPASSWORD odoo-db-1 psql -h 127.0.0.1 -U odoo_staging -d jats_staging -Atc "select 1" 2>&1 | tail -1
docker compose up -d --no-deps odoo-staging
