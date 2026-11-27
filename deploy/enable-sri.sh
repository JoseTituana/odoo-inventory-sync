#!/usr/bin/env bash
# Add SRI electronic invoicing to a running Odoo on the server.
#
#   enable-sri.sh staging      # jats_staging, through the SSH tunnel (8070)
#   enable-sri.sh production   # jats, the store; makes a backup first
#
# Run from /opt/odoo after copying Dockerfile, compose.yaml, sri-configure.py,
# this script and addons/l10n_ec_pos_sri there (deploy/enable-sri.ps1 does it
# from the store PC). It can run again safely: it skips what is already done
# and updates the module.
#
# It never touches the electronic signature (.p12) or its password, and it
# never switches the SRI environment to production: the owner does both in
# Odoo. A freshly installed company stays on the SRI test environment.
set -euo pipefail

target=${1:-}
case "$target" in
  staging)    service=odoo-staging; db=jats_staging; conf=config-staging/odoo.conf ;;
  production) service=odoo;         db=jats;         conf=config/odoo.conf ;;
  *) echo "usage: $0 staging|production" >&2; exit 2 ;;
esac

cd "$(dirname "$0")"
step() { echo; echo "== $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

for f in Dockerfile compose.yaml sri-configure.py "$conf" addons/l10n_ec_pos_sri/__manifest__.py; do
  [[ -e $f ]] || fail "missing $PWD/$f"
done
grep -q "build: ." compose.yaml || fail "compose.yaml does not build the invoicing image; copy the new one"

step "1/6 Settings: add the OCA modules to addons_path in $conf"
if sudo grep -q "^addons_path.*/mnt/oca-addons" "$conf"; then
  echo "already there"
else
  sudo grep -q "^addons_path" "$conf" || fail "$conf has no addons_path line"
  sudo cp -p "$conf" "$conf.before-sri"
  # Only the addons_path line changes; the file holds passwords, never print it.
  sudo sed -i 's#^\(addons_path *= *\)/mnt/extra-addons,#\1/mnt/extra-addons,/mnt/oca-addons,#' "$conf"
  sudo grep -q "^addons_path.*/mnt/oca-addons" "$conf" \
    || fail "could not add /mnt/oca-addons; expected 'addons_path = /mnt/extra-addons,...'. Saved copy: $conf.before-sri"
  echo "done (copy of the old file: $conf.before-sri)"
fi
sudo chown -R 100:101 addons/l10n_ec_pos_sri

step "2/6 Build the image with the invoicing libraries (a few minutes)"
docker compose build "$service"

if [[ $target == production ]]; then
  step "3/6 Backup of jats before changing it"
  sudo /usr/local/sbin/odoo-backup
  ls -1t /opt/backups/backup-*.tar.gz | head -1
else
  step "3/6 Backup: not needed for staging"
fi

step "4/6 Install or update the invoicing modules in $db"
docker compose stop "$service"
log=$(mktemp)
if ! docker compose run --rm --no-deps -T "$service" \
    odoo -c /etc/odoo/odoo.conf -d "$db" -i l10n_ec_pos_sri -u l10n_ec_pos_sri \
    --stop-after-init --no-http </dev/null >"$log" 2>&1 \
    || grep -qE " (ERROR|CRITICAL) " "$log"; then
  grep -E -A20 " (ERROR|CRITICAL) " "$log" | head -60 >&2 || tail -40 "$log" >&2
  echo "Starting $service again as it was." >&2
  docker compose up -d --no-deps "$service" >&2
  fail "installing the modules failed; $db was not changed (log: $log)"
fi
rm -f "$log"
echo "installed"

step "5/6 Configure the POS for invoices (journal 001-001, payment codes, receipt QR off)"
docker compose run --rm --no-deps -T -e SRI_ALLOW_DB="$db" "$service" \
  odoo shell -c /etc/odoo/odoo.conf -d "$db" --no-http --log-level=warn < sri-configure.py

step "6/6 Start $service"
docker compose up -d --no-deps "$service"
for _ in $(seq 60); do
  docker compose exec -T "$service" python3 -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8069/web/login', timeout=5)" \
    >/dev/null 2>&1 && break
  sleep 2
done
docker compose ps "$service"

echo
echo "OK: invoicing is installed in $db. Next, in Odoo: upload the .p12, type its"
echo "password and check the SRI environment (see the guide)."
