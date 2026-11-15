# odoo-inventory-sync

A small Python tool that syncs a product CSV with [Odoo](https://www.odoo.com) through its external XML-RPC API. It creates new products, updates changed names and prices, and can optionally adjust on-hand stock. It has no runtime dependencies.

It comes from a real use case: connecting a custom inventory system and a point of sale to a self-hosted Odoo ERP using the Odoo API and Python, with no plugin.

## Features

- Matches products by SKU (`default_code` in Odoo).
- `--dry-run` shows what would change without writing anything.
- Validates the CSV first: missing columns, empty SKU, duplicates, negative or non-numeric values. Bad lines are reported and skipped.
- Retries network errors with exponential backoff. Odoo errors (access denied, validation) are not retried.
- One failing row never stops the whole run; errors are listed at the end.
- Credentials come from environment variables or a local `.env` file that is never committed.

## Install

```bash
git clone https://github.com/<your-user>/odoo-inventory-sync.git
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
```

## Roadmap

- Batch lookups instead of one request per SKU.
- Support product categories and barcodes.
- Structured logging and a JSON report.

## License

MIT
