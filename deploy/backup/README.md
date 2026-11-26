# Backups

Nightly backups of Odoo and n8n, with an off-server copy on the store's POS PC.

```
02:00 Ecuador (07:00 UTC)                 every 3 h + at startup
server: odoo-backup  ──►  /opt/backups  ◄──  POS PC: pull-backups.ps1  ──►  D:\Respaldos\droplet
        keeps 7 days       (read-only SFTP,          keeps 60 days
                            user bkpull)
```

## What a backup contains

One file per night, `backup-YYYY-MM-DD_HHMM.tar.gz` (server time, UTC), plus a
`.sha256` checksum written last. A bundle without its checksum is incomplete.

| Inside the bundle | Source |
|---|---|
| `odoo-db.dump` | `pg_dump -Fc` of the `jats` database |
| `odoo-filestore.tar` | Odoo filestore (images, attachments) |
| `n8n/database.sqlite` | n8n database, copied with SQLite's online backup API |
| `n8n/files.tar` | other n8n files (includes its encryption key) |

**Not included:** `/opt/odoo/.env` and `config/odoo.conf`. Their passwords live in
the password manager. Restoring on a new server needs them.

The bundle includes customer, price and sales data and the n8n credentials
(encrypted, but stored with their key). Treat every copy as sensitive.

## Files

| File | Where it goes |
|---|---|
| `backup.sh` | `/usr/local/sbin/odoo-backup` (root, 750) |
| `odoo-backup.cron` | `/etc/cron.d/odoo-backup` (root, 644) |
| `odoo-backup.logrotate` | `/etc/logrotate.d/odoo-backup` |
| `restore-test.sh` | `/usr/local/sbin/odoo-restore-test` |
| `setup-pull-user.sh` | `/usr/local/sbin/odoo-setup-pull-user` |
| `pull-backups.ps1`, `install-pos.ps1` | POS PC, run once as administrator |

Log on the server: `/var/log/odoo-backup.log`. Log on the PC: `D:\Respaldos\droplet\pull.log`.

## Server setup

```bash
install -o root -g root -m 750 backup.sh /usr/local/sbin/odoo-backup
install -o root -g root -m 750 restore-test.sh /usr/local/sbin/odoo-restore-test
install -o root -g root -m 750 setup-pull-user.sh /usr/local/sbin/odoo-setup-pull-user
install -o root -g root -m 644 odoo-backup.cron /etc/cron.d/odoo-backup
install -o root -g root -m 644 odoo-backup.logrotate /etc/logrotate.d/odoo-backup
odoo-backup          # first run by hand
```

## POS PC setup

Requirements: Windows with the "OpenSSH Client" feature, BitLocker on the disk
that holds the backups, and staff using **standard** (non-administrator) accounts.

1. Copy `pull-backups.ps1` and `install-pos.ps1` to the PC.
2. Open PowerShell **as administrator** in that folder and run:
   `powershell -ExecutionPolicy Bypass -File .\install-pos.ps1`
   (add `-Destination E:\Respaldos\droplet` to use another disk).
3. It prints a public key. On the server, run:
   `odoo-setup-pull-user 'ssh-ed25519 AAAA... bkpull@POS-PC'`
4. Test: `Start-ScheduledTask -TaskName 'Odoo backup download'`, then read `pull.log`.

The task runs as SYSTEM. `C:\OdooBackup` (script and key) and the backup folder are
readable only by SYSTEM and Administrators. The key can only read `/opt/backups`
over SFTP: no shell, no forwarding, no writes. To revoke a PC, delete its line from
`/var/lib/bkpull/.ssh/authorized_keys` on the server.

## Alerts

A failed backup should not wait for someone to read a log. Both sides can send
an alert as a JSON POST `{"text": "...", "host": "..."}` to a webhook, for example
an n8n workflow (already on the server) that forwards it to Telegram, WhatsApp
or email.

**Server** (`/etc/default/odoo-backup`, owner root, mode 600):

```bash
ALERT_URL=https://n8n.example.com/webhook/backup-alert   # POST when a backup fails
HEARTBEAT_URL=https://hc-ping.com/<uuid>                 # optional, see below
```

The alert names the reason (`database dump is not readable`, or the failing line
for an unexpected error). `HEARTBEAT_URL` is pinged after every good backup and at
`HEARTBEAT_URL/fail` after a bad one. A Healthchecks.io-style service then also
alerts when **no** backup ran at all (server off, cron broken), which the script
itself cannot notice. Without these settings nothing is sent.

**POS PC**: put the same webhook URL on the first line of
`C:\OdooBackup\alert-url.txt` (or pass `-AlertUrl`). `pull-backups.ps1` posts
when a download fails verification, the server cannot be reached, or the newest
backup on the PC is older than `-MaxAgeHours` (48 by default).

Test the server side with `ALERT_URL=... DB_NAME=does-not-exist odoo-backup`: the
dump fails and the alert arrives.

## Restore

**Test a backup** (safe: restores into a separate database, never `jats`):

```bash
odoo-restore-test /opt/backups/backup-YYYY-MM-DD_HHMM.tar.gz jats_staging
```

It verifies the checksum, restores the database and filestore, compares row counts
with `jats`, and checks the n8n copy. The target database must not exist yet.

**Real restore** (after data loss) replaces production data, so plan it first:
stop Odoo, keep a fresh backup of the current state, restore, then start Odoo.
Write the exact steps down at the time and have a second person check them.
On a new server, copy the bundle from the POS PC with `scp`, install Odoo with
`deploy/` and the passwords from the password manager, then restore.

## What a backup protects against

| Event | Recovery | Data lost |
|---|---|---|
| Wrong edit or deletion in Odoo | restore last night's backup | since 02:00 |
| Damaged database | same | since 02:00 |
| Server lost | new server + bundle from the POS PC | since the last bundle the PC downloaded |
| POS PC lost or stolen | server still has 7 days; revoke the PC's key | none |
