"""Run deploy/backup/backup.sh with a fake `docker` and check its alerts."""
import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "backup" / "backup.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash") or not shutil.which("flock") or not shutil.which("curl"),
    reason="needs a Linux shell with flock and curl",
)

FAKE_DOCKER = """#!/usr/bin/env bash
# pg_dump writes a fake dump; pg_restore --list fails when FAKE_BAD_DUMP is set.
case "$*" in
  *pg_dump*) [ -z "${FAKE_DUMP_CRASH:-}" ] || exit 3; echo "fake dump" ;;
  *pg_restore*) cat >/dev/null; [ -z "${FAKE_BAD_DUMP:-}" ] ;;
esac
"""


@pytest.fixture
def server():
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            received.append((self.command, self.path, body))
            self.send_response(200)
            self.end_headers()

        do_GET = do_POST = _record

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", received
    httpd.shutdown()


def run_backup(tmp_path, base_url, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    config = tmp_path / "odoo-backup.conf"
    config.write_text(f"ALERT_URL={base_url}/alert\nHEARTBEAT_URL={base_url}/ping\n")
    full_env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        CONFIG_FILE=str(config),
        BACKUP_DIR=str(tmp_path / "backups"),
        LOCK_FILE=str(tmp_path / "backup.lock"),
        ODOO_DATA=str(tmp_path / "none"),
        N8N_DATA=str(tmp_path / "none"),
        MIN_FREE_MB="1",
        READ_GROUP="no-such-group-xyz",
    )
    full_env.update(env)
    return subprocess.run(["bash", str(SCRIPT)], env=full_env, capture_output=True, text=True)


def test_good_backup_pings_heartbeat_and_sends_no_alert(tmp_path, server):
    url, received = server
    result = run_backup(tmp_path, url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [(m, p) for m, p, _ in received] == [("GET", "/ping")]
    bundles = list((tmp_path / "backups").glob("backup-*.tar.gz"))
    assert len(bundles) == 1
    assert (tmp_path / "backups" / f"{bundles[0].name}.sha256").exists()


def test_failed_backup_sends_alert_with_reason(tmp_path, server):
    url, received = server
    result = run_backup(tmp_path, url, FAKE_BAD_DUMP="1")
    assert result.returncode != 0
    alerts = [json.loads(b) for m, p, b in received if p == "/alert"]
    assert len(alerts) == 1
    assert "FALLÓ" in alerts[0]["text"]
    assert "database dump is not readable" in alerts[0]["text"]
    assert ("GET", "/ping/fail") in [(m, p) for m, p, _ in received]
    # Nothing half-written is left behind.
    assert not list((tmp_path / "backups").glob("*.partial"))
    assert not list((tmp_path / "backups").glob(".work-*"))


def test_unexpected_error_is_reported_with_its_line(tmp_path, server):
    url, received = server
    result = run_backup(tmp_path, url, FAKE_DUMP_CRASH="1")
    assert result.returncode != 0
    alerts = [json.loads(b) for m, p, b in received if p == "/alert"]
    assert len(alerts) == 1
    assert "line" in alerts[0]["text"] and "pg_dump" in alerts[0]["text"]


def test_without_urls_nothing_is_sent(tmp_path, server):
    url, received = server
    result = run_backup(tmp_path, url, CONFIG_FILE=str(tmp_path / "missing.conf"))
    assert result.returncode == 0, result.stderr
    assert received == []
