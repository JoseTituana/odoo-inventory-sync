"""Prepare the e2e database: Spanish, prices with VAT included, the example
catalog imported with odoo-sync, and a till with a salesperson and a manager.

Writes e2e/fixtures.json with the ids and barcodes the browser test uses.
"""
import json
import os
import subprocess
import sys
import xmlrpc.client
from pathlib import Path

URL = os.environ.get("E2E_URL", "http://localhost:8079")
DB = os.environ.get("E2E_DB", "e2e")
USER = PASSWORD = "admin"
ROOT = Path(__file__).resolve().parent.parent

common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common")
uid = common.authenticate(DB, USER, PASSWORD, {})
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(model, method, *args, **kwargs):
    return models.execute_kw(DB, uid, PASSWORD, model, method, list(args), kwargs)


# Spanish UI and prices that already include VAT, as at the store.
company_id = call("res.users", "read", [uid], ["company_id"])[0]["company_id"][0]
call("res.company", "write", [company_id], {"account_price_include": "tax_included", "name": "Calzado Josheps"})
call("res.users", "write", [uid], {"lang": "es_EC", "tz": "America/Guayaquil"})

# The catalog goes in through the real importer.
env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), ODOO_URL=URL, ODOO_DB=DB, ODOO_USER=USER, ODOO_PASSWORD=PASSWORD)
subprocess.run(
    [sys.executable, "-m", "odoo_inventory_sync.cli", "catalog", str(ROOT / "examples/catalogo-prueba.xlsx"), "--yes"],
    env=env,
    check=True,
)


def variant(code):
    found = call("product.product", "search_read", [["default_code", "=", code]],
                 fields=["id", "barcode", "qty_available", "lst_price"])
    assert found, f"{code} was not imported"
    return found[0]


in_stock = variant("VALENTINA-NEG-CUE-37")   # 5 pairs
one_left = variant("VALENTINA-NEG-CUE-35")   # 1 pair
no_stock = variant("VALENTINA-NEG-CUE-38")   # 0 pairs
assert (in_stock["qty_available"], one_left["qty_available"], no_stock["qty_available"]) == (5, 1, 0)

# Till: cash and bank payment methods, one salesperson and one manager.
# (Search before creating, so the script can be run again on the same database.)
def ensure(model, domain, values):
    found = call(model, "search", domain, limit=1)
    return found[0] if found else call(model, "create", values)


cash = ensure("account.journal", [["type", "=", "cash"]], {"name": "Efectivo", "type": "cash", "code": "EFE"})
bank = ensure("account.journal", [["type", "=", "bank"]], {"name": "Banco", "type": "bank", "code": "BNK"})
methods = [
    ensure("pos.payment.method", [["name", "=", "Efectivo"]],
           {"name": "Efectivo", "journal_id": cash, "is_cash_count": True}),
    ensure("pos.payment.method", [["name", "=", "Tarjeta"]], {"name": "Tarjeta", "journal_id": bank}),
]
manager = ensure("hr.employee", [["name", "=", "Encargado"]], {"name": "Encargado", "pin": "4321"})
seller = ensure("hr.employee", [["name", "=", "Vendedor"]], {"name": "Vendedor", "pin": "1111"})
config = ensure("pos.config", [["name", "=", "Local Principal"]], {
    "name": "Local Principal",
    "payment_method_ids": [(6, 0, methods)],
    "module_pos_hr": True,
})
call("pos.config", "write", [config], {
    "advanced_employee_ids": [(6, 0, [manager])],
    "basic_employee_ids": [(6, 0, [seller])],
})

fixtures = {
    "url": URL,
    "db": DB,
    "config_id": config,
    "in_stock": in_stock,
    "one_left": one_left,
    "no_stock": no_stock,
}
(ROOT / "e2e/fixtures.json").write_text(json.dumps(fixtures, indent=2))
print(json.dumps(fixtures, indent=2))
