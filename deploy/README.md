# Deployment

How the development Odoo server for this project runs: Odoo 19 Community and PostgreSQL 16 in Docker, behind Caddy for HTTPS, on a small Ubuntu 24.04 server shared with other services.

## Design choices

- **Odoo is never exposed directly.** It listens on `127.0.0.1:8069`; only the reverse proxy is reachable from the internet. PostgreSQL has no published port at all.
- **Memory limits protect neighbors.** On a 2 GB server shared with other apps, `mem_limit` makes Docker restart Odoo if it grows too much, instead of the kernel killing another service. A 2 GB swap file adds headroom.
- **Low-memory settings.** Odoo runs in threaded mode (`workers = 0`) and PostgreSQL uses small buffers. Idle usage is about 110 MB for Odoo and 60 MB for PostgreSQL.
- **Database manager closed.** `list_db = False` and `dbfilter` keep the public site from listing, creating or deleting databases.
- **Secrets stay on the server.** `.env` and `config/odoo.conf` are created from the examples on the server with random passwords and are readable by root only. They are never committed.

## Setup

On the server, as root:

```bash
mkdir -p /opt/odoo/config /opt/odoo/addons
cp compose.yaml .env.example odoo.conf.example /opt/odoo/
cd /opt/odoo
mv .env.example .env
mv odoo.conf.example config/odoo.conf
# Replace every CHANGE_ME, using long random values, for example:
openssl rand -hex 24
chmod 600 .env
chown 100:101 config/odoo.conf addons   # odoo user inside the image
chmod 640 config/odoo.conf
docker compose up -d
```

To create the first database, set `list_db = True`, restart Odoo, create the database at `/web/database/manager`, then set `list_db = False` and `dbfilter` to the database name and restart again:

```bash
docker compose restart odoo
```

## HTTPS with Caddy

Point a DNS `A` record at the server, then add a site to `/etc/caddy/Caddyfile`:

```
odoo.example.com {
    reverse_proxy 127.0.0.1:8069
}
```

Validate and reload. Caddy obtains the certificate automatically:

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
```

## Custom modules

Odoo modules from this repository (such as the POS stock lookup, M3 in the [project plan](../docs/project-plan.md)) go in `/opt/odoo/addons`, which is mounted at `/mnt/extra-addons`.
