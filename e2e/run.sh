#!/usr/bin/env bash
# End-to-end test of the point of sale with pos_stock_guard, in a real browser.
#
# Starts a throwaway PostgreSQL + Odoo 19 in Docker, creates a database with
# the module, imports examples/catalogo-prueba.xlsx with odoo-sync, sets up a
# till, then drives it with Playwright (e2e/pos.spec.js).
#
# Needs: docker, python with this package installed (pip install -e .), node.
# Usage: e2e/run.sh            (KEEP=1 leaves the containers running)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${E2E_PORT:-8079}"
NET=e2e-net
DB=e2e
export E2E_URL="http://localhost:${PORT}" E2E_DB="$DB"

remove() {
    docker rm -f e2e-odoo e2e-db >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}
remove
[[ "${KEEP:-0}" == 1 ]] || trap remove EXIT

docker network create "$NET" >/dev/null
docker run -d --name e2e-db --network "$NET" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres postgres:16 >/dev/null
until docker exec e2e-db pg_isready -U odoo >/dev/null 2>&1; do sleep 1; done

# The image's entrypoint passes the database host and user from these variables.
DBENV=(-e HOST=e2e-db -e USER=odoo -e PASSWORD=odoo)
ODOO=(-d "$DB" --db-filter="^${DB}\$")
echo "== creating database with pos_stock_guard"
rc=0
docker run --rm --network "$NET" "${DBENV[@]}" -v "$PWD/addons:/mnt/extra-addons:ro" odoo:19 \
    odoo "${ODOO[@]}" -i pos_stock_guard --without-demo=all --load-language=es_EC \
    --stop-after-init --no-http </dev/null >e2e/init.log 2>&1 || rc=$?
if [[ $rc != 0 ]] || grep -qE " (ERROR|CRITICAL) " e2e/init.log; then
    tail -40 e2e/init.log >&2
    echo "creating the database failed" >&2
    exit 1
fi

docker run -d --name e2e-odoo --network "$NET" "${DBENV[@]}" -p "${PORT}:8069" \
    -v "$PWD/addons:/mnt/extra-addons:ro" odoo:19 odoo "${ODOO[@]}" >/dev/null
for _ in $(seq 90); do
    curl -sf "${E2E_URL}/web/login" >/dev/null && break
    if [[ "$(docker inspect -f '{{.State.Running}}' e2e-odoo)" != true ]]; then
        docker logs e2e-odoo 2>&1 | tail -20 >&2
        exit 1
    fi
    sleep 2
done
curl -sf "${E2E_URL}/web/login" >/dev/null || { echo "Odoo did not start" >&2; exit 1; }

echo "== importing the example catalog and setting up the till"
python e2e/setup.py

echo "== browser test"
# Only errors logged while the till is used count. (The import above goes
# through XML-RPC with a password, and Odoo logs an error when
# stock.quant.action_apply_inventory returns None over XML-RPC.)
skip=$(docker logs e2e-odoo 2>&1 | wc -l)
(cd e2e && npx playwright test --trace off)

docker logs e2e-odoo 2>&1 | tail -n +"$((skip + 1))" >e2e/odoo.log
if grep -qE " (ERROR|CRITICAL) " e2e/odoo.log; then
    grep -E -A25 " (ERROR|CRITICAL) " e2e/odoo.log | head -120 >&2
    echo "Odoo logged errors during the test" >&2
    exit 1
fi
