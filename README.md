# odoo-inventory-sync

[![tests](https://github.com/JoseTituana/odoo-inventory-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/JoseTituana/odoo-inventory-sync/actions/workflows/tests.yml)

Tools that run a footwear business on a self-hosted [Odoo 19](https://www.odoo.com) Community through its external XML-RPC API:

- **Catalog import:** a spreadsheet of models, colors, materials and sizes becomes Odoo product variants with SKUs, internal EAN-13 barcodes and counted stock, in one run.
- **Stock-based labels:** one label per pair actually in stock, as a PDF for adhesive A4/Letter sheets or a thermal roll. Odoo itself only prints labels per product or per received quantity.
- **POS stock guard:** an Odoo module that blocks point of sale sales of out-of-stock variants unless a manager authorizes them with a PIN.
- **Ecuadorian electronic invoices from the POS:** a bridge between the point of sale and the free OCA module `l10n_ec_account_edi`, which signs invoices and sends them to the SRI. No paid provider.
- **Simple product sync:** the original CSV sync for products without variants.
- **Deployment:** Odoo + PostgreSQL with Docker Compose behind Caddy, nightly backups with an off-server copy, and an isolated staging Odoo.

It comes from a real use case: Calzado Josheps, a shoe factory with its own store.

Everything users see is in Spanish: the command line messages and help, and the module's point of sale dialogs, report and menu (`i18n/es.po`, used when the Odoo user's language is any Spanish variant).

## Install

```bash
git clone https://github.com/JoseTituana/odoo-inventory-sync.git
cd odoo-inventory-sync
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env     # then set the Odoo URL, database, user and API key
```

On the store's Windows PCs use `deploy/pos-pc/install-importer.ps1` instead: see [deploy/pos-pc/README.md](deploy/pos-pc/README.md). Without a `.env` in the current folder, the settings are read from `%APPDATA%\odoo-sync\.env` (`~/.config/odoo-sync/.env` on Linux and macOS).

Use an API key, not a password, and a dedicated Odoo user with Inventory administrator rights only. `.env` is ignored by git.

The tools talk to Odoo through its JSON-2 API (`/json/2`, Odoo 19 and later, needs the API key) and fall back to XML-RPC on older servers or when given a password. XML-RPC is deprecated in Odoo 19 and removed in Odoo 22. Set `ODOO_PROTOCOL=json2` or `xmlrpc` to force one.

## Catalog import

One row per model × color × material × size. Spanish or English headers are accepted, case and extra spaces in values do not matter, and `.xlsx` is read directly (CSV with `,` or `;` and decimal commas also works). See `examples/catalogo-prueba.xlsx`.

| Modelo | Color | Material | Talla | Precio | Costo | Cantidad | Categoria |
|---|---|---|---|---|---|---|---|
| Valentina | Negro | Cuero | 36 | 48 | 30 | 3 | Calzado / Dama |

```bash
odoo-sync catalog inventario.xlsx --dry-run -v   # preview: errors by line and every change
odoo-sync catalog inventario.xlsx                # apply
```

What it does:

- One product template per model, with attributes Color, Material and Talla (created with Variant Creation = Instantly when missing). Every combination of the model's values becomes a variant; combinations without a row get stock 0.
- Each variant gets a SKU such as `VALENTINA-NEG-CUE-36` and an internal EAN-13 barcode (prefix `2`, reserved for in-store use). Existing SKUs and barcodes are never changed, so printed labels stay valid.
- Stock is set with an inventory adjustment at the main warehouse (or `--location-id`), only where the counted quantity differs, and read back to confirm it was applied.
- The file is a full count of every model in it: a variant of that model without a row was not found, so its stock is set to 0 (shown as `(not in the file)`). Models that are not in the file are left alone, so you can count one category per night.
- Price and cost are per model; categories such as `Calzado / Dama` are created when missing; models are made available in the point of sale.

Safety rules:

- Any error in the file (empty cell, text in a number, half a pair, two rows for the same variant, two prices for one model) stops the import before anything is written.
- A model whose attributes are not exactly Color, Material and Talla is skipped with an error: adding or removing an attribute makes Odoo delete and recreate its variants.
- An archived product with the same name is reported, never duplicated.
- Running the same file twice changes nothing.
- Stock moves after the count (sales, refunds, receipts) are listed before anything is written, because applying the count would undo them. The count time is when the file was last saved, or `--counted-at "2026-11-27 21:30"` (local time). The import then asks for confirmation; `--yes` skips the question, and without a terminal it stops.

## Labels from stock

```bash
odoo-sync labels --dry-run -v                                  # count labels per variant
odoo-sync labels --profile a4-21 --brand "Calzado Josheps"     # PDF for an A4 sheet of 21 labels
odoo-sync labels --model Valentina --start-at 8                # one model, from position 8 of a used sheet
```

| Profile | Use |
|---|---|
| `a4-21` | A4 sheet, 3 × 7 labels of 63.5 × 38.1 mm |
| `letter-30` | Letter sheet, 3 × 10 labels of 66.7 × 25.4 mm (Avery 5160 type) |
| `roll-50x30` | Thermal roll, one 50 × 30 mm label per page |

Each label shows the model, color and material, the SKU, a large size and price, and the EAN-13 barcode. Zero or negative stock prints nothing; variants without a barcode are skipped with a warning. Print at 100 % (actual size); test first on plain paper with `--outlines`.

## POS stock guard (Odoo module)

`addons/pos_stock_guard` depends on `point_of_sale` and `pos_hr`. When a cashier adds a variant, or presses Payment, it compares the quantity on the order with the stock at the point of sale's location (minus sales of open sessions that have not moved stock yet). If stock is short, the sale is blocked until a manager enters their employee PIN:

- Managers are the employees in **Advanced rights** of that point of sale, plus point of sale administrators, with a PIN set.
- The PIN is checked on the server, never in the browser. Five wrong PINs in 10 minutes lock authorizations for that point of sale.
- Every authorization and failed attempt is listed in **Point of Sale > Reporting > Out-of-stock authorizations**.
- Offline, the till checks the last stock it downloaded (when it opened and on every check), minus the paid orders it has not sent yet. Sales within that stock go through and appear in the same report as **Sold offline** once the connection is back; review them and recount. Sales beyond it stay blocked, because a manager PIN can only be checked on the server.

Install: copy the folder to the Odoo addons path, restart Odoo, then install **POS Stock Guard** from Apps. After updating the folder, upgrade the module (Apps > POS Stock Guard > Upgrade, or `odoo -u pos_stock_guard`) and reload the point of sale. Its tests run inside Odoo:

```bash
odoo -d <test_db> -i pos_stock_guard --test-enable --test-tags /pos_stock_guard --stop-after-init
```

Both Odoo modules are also tested on every push by the `odoo-modules` GitHub workflow, inside the same Docker image the server runs.

## Electronic invoices (SRI, Ecuador)

Only the orders the cashier marks as **Invoice** become electronic invoices; every other sale stays a normal receipt. The OCA module [`l10n_ec_account_edi`](https://github.com/OCA/l10n-ecuador) (AGPL-3) builds, signs and sends the XML to the SRI; `addons/l10n_ec_pos_sri` connects it to the point of sale:

- Each POS payment method has an SRI payment method (`formaPago`): cash `01`, bank `20` by default, credit card `19` if you choose it.
- Before an order to invoice is validated, the customer must have an identification number, and Consumidor Final cannot exceed the limit enforced by the OCA module (USD 50 by default, parameter `l10n_ec_final_consumer_limit`).
- If Odoo cannot create the invoice (for example, a missing certificate), the sale is still saved, the POS shows its usual invoice error, and the error is written on the order so it can be invoiced later from the back office.
- Point of Sale > Orders gets the filters **Not Invoiced** and **Invoice Failed** next to the standard **Invoiced**, so receipts and invoices can be filtered by date and exported separately for the accountant.
- The invoice is sent to the SRI by the `account_edi` scheduled job, so a slow or unavailable SRI never stops the cashier.

The OCA modules and their Python libraries are part of the Docker image (`deploy/Dockerfile`). Setup and testing in staging: [docs/sri-einvoicing.md](docs/sri-einvoicing.md).

## Simple product sync

```bash
odoo-sync examples/products.csv --dry-run                  # columns: sku,name,price,qty
odoo-sync examples/products.csv --stock-location-id 8
```

Exit codes for every command: `0` success, `1` some rows or models failed, `2` bad configuration or file.

## Test

```bash
pytest -q
```

The Python tests use in-memory fakes of Odoo, so they run anywhere. The label tests render each profile and scan the printed barcode back with a software reader at 203 dpi. Everything was also run against a staging Odoo 19 before production.

## Project layout

```
src/odoo_inventory_sync/
  client.py        XML-RPC wrapper with retries
  catalog.py       spreadsheet parsing, validation, SKU and EAN-13 codes
  catalog_sync.py  applies the catalog to Odoo (variants, codes, stock)
  labels.py        stock to labels, label profiles, PDF rendering
  sync.py          simple product CSV sync
  cli.py           command line interface
addons/pos_stock_guard/   Odoo module for the point of sale
tests/             pytest suite
examples/          sample files
docs/              project plan
deploy/            Docker Compose, backups, staging
```

## License

MIT
