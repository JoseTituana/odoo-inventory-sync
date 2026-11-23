# odoo-inventory-sync

[![tests](https://github.com/JoseTituana/odoo-inventory-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/JoseTituana/odoo-inventory-sync/actions/workflows/tests.yml)

A small Python tool that syncs a product CSV with [Odoo](https://www.odoo.com) through its external XML-RPC API. It creates new products, updates changed names and prices, and can optionally adjust on-hand stock. It has no runtime dependencies.

It comes from a real use case: connecting a custom inventory system and a point of sale to a self-hosted Odoo ERP using the Odoo API and Python, with no plugin.

## Features

- Matches products by SKU (`default_code` in Odoo).
- `--dry-run` shows what would change without writing anything.
- Validates the CSV first: missing columns, short rows, empty SKU, duplicates, negative, non-numeric or non-finite values. Files saved by Excel (UTF-8 with BOM) are accepted. Bad lines are reported and skipped.
- Retries network errors with exponential backoff. Odoo errors (access denied, validation) are not retried.
- One failing row never stops the whole run; errors are listed at the end.
- Credentials come from environment variables or a local `.env` file that is never committed.

## Install

```bash
git clone https://github.com/JoseTituana/odoo-inventory-sync.git
cd odoo-inventory-sync
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Configure

```bash
cp .env.example .env     # then edit .env with your Odoo URL, database, user and API key
```

## Use

CSV format (`examples/products.csv`):

```csv
sku,name,price,qty
SH-001,Leather Loafer,45.00,12
```

```bash
odoo-sync examples/products.csv --dry-run          # preview
odoo-sync examples/products.csv                     # apply product changes
odoo-sync examples/products.csv --stock-location-id 8   # also adjust stock in location 8
```

Exit codes: `0` success, `1` some rows failed, `2` bad configuration or CSV.

## Test

```bash
pytest -q
```

Tests use an in-memory fake instead of a real Odoo server, so they run anywhere. Stock adjustment follows Odoo's inventory flow (`stock.quant` with `inventory_quantity`, then `action_apply_inventory`); verify it against your Odoo version before using it on production data.

## Project layout

```
src/odoo_inventory_sync/
  client.py   XML-RPC wrapper with retries
  sync.py     CSV parsing and create/update logic
  cli.py      command line interface
tests/        pytest suite
docs/         project plan
deploy/       Odoo 19 + PostgreSQL with Docker Compose, behind Caddy
```

## Roadmap

The project is growing into a toolkit for a footwear business on Odoo. See the full [project plan](docs/project-plan.md).

- **M0** Development environment: Odoo Community in Docker with sample shoes.
- **M1** Matrix catalog import: one model with color and size variants, generated SKU and EAN-13 barcode.
- **M2** Stock-based labels: one label per pair in stock, printer profiles for thermal rolls and A4/Letter sheets (PDF, then ZPL and TSPL).
- **M3** POS stock lookup: type a model code in the point of sale and see a size × color stock grid.
- **M4** Channels and reports: store, wholesale and warehouse 2.

## License

MIT
