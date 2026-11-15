"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .client import OdooClient, OdooError
from .sync import load_rows, sync_products


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines into the environment without overriding it."""
    env_file = Path(path)
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odoo-sync", description="Sync a product CSV with Odoo through XML-RPC."
    )
    parser.add_argument("csv_path", help="CSV with columns: sku,name,price,qty")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    parser.add_argument(
        "--stock-location-id",
        type=int,
        help="Odoo stock.location id; if set, on-hand quantity is adjusted too",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    missing = [k for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        rows, csv_errors = load_rows(args.csv_path)
    except (OSError, ValueError) as error:
        print(f"Cannot read CSV: {error}", file=sys.stderr)
        return 2

    for message in csv_errors:
        print(f"skipped {message}", file=sys.stderr)

    client = OdooClient(
        os.environ["ODOO_URL"],
        os.environ["ODOO_DB"],
        os.environ["ODOO_USER"],
        os.environ["ODOO_PASSWORD"],
    )
    try:
        client.authenticate()  # fail fast on bad credentials instead of once per row
        result = sync_products(
            client, rows, dry_run=args.dry_run, stock_location_id=args.stock_location_id
        )
    except OdooError as error:
        print(f"Odoo error: {error}", file=sys.stderr)
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    print(prefix + result.summary())
    for message in result.errors:
        print(f"error {message}", file=sys.stderr)
    return 1 if (result.errors or csv_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
