import os

import pytest

from odoo_inventory_sync import cli
from odoo_inventory_sync.catalog import read_table

KEYS = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


def test_env_with_bom_is_read(clean_env):
    (clean_env / "work" / ".env").write_bytes("﻿ODOO_URL=https://x\r\nODOO_DB=d\r\n".encode("utf-8"))
    cli.load_dotenv()
    assert os.environ["ODOO_URL"] == "https://x"
    assert os.environ["ODOO_DB"] == "d"


def test_per_user_file_is_used_when_folder_has_none(clean_env):
    target = cli.default_env_file()
    target.parent.mkdir(parents=True)
    target.write_text("ODOO_USER=importer\n", encoding="utf-8")
    cli.load_dotenv()
    assert os.environ["ODOO_USER"] == "importer"


def test_folder_env_wins_over_per_user_file(clean_env):
    target = cli.default_env_file()
    target.parent.mkdir(parents=True)
    target.write_text("ODOO_DB=user_file\n", encoding="utf-8")
    (clean_env / "work" / ".env").write_text("ODOO_DB=folder\n", encoding="utf-8")
    cli.load_dotenv()
    assert os.environ["ODOO_DB"] == "folder"


def test_missing_settings_say_where_to_write_them(clean_env, capsys):
    assert cli.main(["labels", "--dry-run"]) != 0
    err = capsys.readouterr().err
    assert "Faltan datos de conexión" in err
    assert str(cli.default_env_file()) in err


def test_cp1252_csv_from_excel_is_read(tmp_path):
    path = tmp_path / "conteo.csv"
    path.write_bytes("Modelo;Color;Talla\nCafé Niño;Marrón;36\n".encode("cp1252"))
    rows = read_table(path)
    assert rows[1][:2] == ["Café Niño", "Marrón"]
