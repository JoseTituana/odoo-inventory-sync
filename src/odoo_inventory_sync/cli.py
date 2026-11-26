"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .catalog import CatalogError, build_plans, load_catalog
from .catalog_sync import CatalogSyncError, OdooCatalogGateway, import_catalog
from .client import OdooClient, OdooError
from .labels import PROFILES, expand, pages_needed, read_stock_labels, render_pdf
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


ENV_KEYS = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD")


def _client_from_env() -> OdooClient | None:
    load_dotenv()
    missing = [k for k in ENV_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return None
    return OdooClient(*(os.environ[k] for k in ENV_KEYS))


def build_catalog_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odoo-sync catalog",
        description="Import a footwear catalog (model x color x material x size) as "
        "product variants with SKUs, internal EAN-13 barcodes and on-hand stock.",
    )
    parser.add_argument("path", help=".xlsx or .csv with columns: model, color, material, "
                        "size, price, cost, qty and optionally category")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    parser.add_argument("--location-id", type=int,
                        help="stock.location id for quantities (default: main warehouse stock)")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every stock change")
    return parser


def catalog_main(argv: list[str]) -> int:
    args = build_catalog_parser().parse_args(argv)
    try:
        rows, errors = load_catalog(args.path)
        plans, plan_errors = build_plans(rows)
    except (OSError, CatalogError) as error:
        print(f"Cannot read catalog: {error}", file=sys.stderr)
        return 2
    errors += plan_errors
    if errors:
        # All or nothing: a partial catalog would leave stock half counted.
        for message in errors:
            print(f"error {message}", file=sys.stderr)
        print(f"{len(errors)} error(s) in the file; nothing was imported.", file=sys.stderr)
        return 2
    pairs = sum(p.pairs for p in plans)
    variants = sum(p.variant_count for p in plans)
    print(f"read {len(rows)} rows: {len(plans)} models, {variants} variants, {pairs} pairs")

    client = _client_from_env()
    if client is None:
        return 2
    try:
        client.authenticate()
        result = import_catalog(OdooCatalogGateway(client), plans, dry_run=args.dry_run,
                                location_id=args.location_id)
    except (OdooError, CatalogSyncError) as error:
        print(f"Odoo error: {error}", file=sys.stderr)
        return 1

    prefix = "[dry-run] " if args.dry_run else ""
    print(prefix + result.summary())
    for label, items in (("new attribute", result.attributes_created),
                         ("new value", result.values_created),
                         ("new category", result.categories_created),
                         ("new model", result.models_created),
                         ("updated model", result.models_updated)):
        for item in items:
            print(f"  {label}: {item}")
    if args.verbose:
        for change in result.stock_changes:
            print(f"  stock {change}")
    for message in result.errors:
        print(f"error {message}", file=sys.stderr)
    return 1 if result.errors else 0


def build_labels_parser() -> argparse.ArgumentParser:
    profiles = "\n".join(f"  {p.name}: {p.description}" for p in PROFILES.values())
    parser = argparse.ArgumentParser(
        prog="odoo-sync labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Print one label per pair in stock, as a PDF.",
        epilog=f"profiles:\n{profiles}\n\nPrint the PDF at 100 %% (actual size), not 'fit to page'.",
    )
    parser.add_argument("--profile", default="a4-21", choices=sorted(PROFILES))
    parser.add_argument("-o", "--output", help="PDF file (default: labels-<profile>-<time>.pdf)")
    parser.add_argument("--model", help="only models whose name contains this text")
    parser.add_argument("--category", help="only categories whose path contains this text")
    parser.add_argument("--location-id", type=int,
                        help="stock.location id (default: main warehouse stock)")
    parser.add_argument("--start-at", type=int, default=1,
                        help="first free position on a partly used sheet (1 = top left)")
    parser.add_argument("--brand", help="text printed at the top of every label")
    parser.add_argument("--outlines", action="store_true",
                        help="draw label borders (for a test print on plain paper)")
    parser.add_argument("--dry-run", action="store_true", help="count labels, write no PDF")
    parser.add_argument("-v", "--verbose", action="store_true", help="list labels per variant")
    return parser


def labels_main(argv: list[str]) -> int:
    args = build_labels_parser().parse_args(argv)
    profile = PROFILES[args.profile]
    if not 1 <= args.start_at <= profile.per_page:
        print(f"--start-at must be between 1 and {profile.per_page} for {profile.name}",
              file=sys.stderr)
        return 2
    client = _client_from_env()
    if client is None:
        return 2
    try:
        client.authenticate()
        stock = read_stock_labels(client, args.location_id, args.model, args.category)
    except (OdooError, ValueError) as error:
        print(f"Odoo error: {error}", file=sys.stderr)
        return 1
    plan = expand(stock)
    pages = pages_needed(len(plan.labels), profile, args.start_at)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}{plan.summary()} -> {pages} page(s) on {profile.name}")
    if args.verbose:
        counts: dict[str, int] = {}
        for label in plan.labels:
            name = label.sku or label.barcode
            counts[name] = counts.get(name, 0) + 1
        for name, count in counts.items():
            print(f"  {count:>3} x {name}")
    for message in plan.skipped:
        print(f"warning {message}", file=sys.stderr)
    if args.dry_run or not plan.labels:
        return 0
    output = args.output or f"labels-{profile.name}-{datetime.now():%Y%m%d-%H%M}.pdf"
    try:
        render_pdf(plan.labels, profile, output, start_at=args.start_at, brand=args.brand,
                   outlines=args.outlines)
    except ValueError as error:
        print(f"Cannot print: {error}", file=sys.stderr)
        return 1
    print(f"wrote {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "catalog":
        return catalog_main(argv[1:])
    if argv and argv[0] == "labels":
        return labels_main(argv[1:])
    args = build_parser().parse_args(argv)
    load_dotenv()

    missing = [k for k in ENV_KEYS if not os.environ.get(k)]
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
