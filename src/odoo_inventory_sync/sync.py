"""Read a product CSV and synchronise it with Odoo."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

REQUIRED_COLUMNS = ("sku", "name", "price", "qty")


class OdooLike(Protocol):
    def search_read(self, model: str, domain: list, fields: list[str], limit: int | None = None) -> list[dict]: ...
    def create(self, model: str, values: dict) -> int: ...
    def write(self, model: str, ids: list[int], values: dict) -> bool: ...
    def call(self, model: str, method: str, *args, **kwargs): ...


@dataclass(frozen=True)
class ProductRow:
    sku: str
    name: str
    price: float
    qty: float


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"created={len(self.created)} updated={len(self.updated)} "
            f"unchanged={len(self.unchanged)} errors={len(self.errors)}"
        )


def load_rows(path: str | Path) -> tuple[list[ProductRow], list[str]]:
    """Parse the CSV. Returns valid rows plus a list of human-readable errors."""
    rows: list[ProductRow] = []
    errors: list[str] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(missing)}")
        for line_no, raw in enumerate(reader, start=2):
            sku = (raw["sku"] or "").strip()
            name = (raw["name"] or "").strip()
            try:
                if not sku or not name:
                    raise ValueError("sku and name are required")
                if sku in seen:
                    raise ValueError(f"duplicate sku {sku}")
                price = float(raw["price"])
                qty = float(raw["qty"])
                if price < 0 or qty < 0:
                    raise ValueError("price and qty must not be negative")
            except ValueError as error:
                errors.append(f"line {line_no}: {error}")
                continue
            seen.add(sku)
            rows.append(ProductRow(sku, name, round(price, 2), qty))
    return rows, errors


def _set_stock(client: OdooLike, product_id: int, qty: float, location_id: int) -> None:
    """Set on-hand quantity using Odoo's inventory adjustment flow."""
    quant_id = client.create(
        "stock.quant",
        {"product_id": product_id, "location_id": location_id, "inventory_quantity": qty},
    )
    client.call("stock.quant", "action_apply_inventory", [quant_id])


def sync_products(
    client: OdooLike,
    rows: list[ProductRow],
    dry_run: bool = False,
    stock_location_id: int | None = None,
) -> SyncResult:
    """Create or update products by SKU (Odoo field ``default_code``)."""
    result = SyncResult()
    for row in rows:
        try:
            found = client.search_read(
                "product.product",
                [["default_code", "=", row.sku]],
                ["id", "name", "list_price"],
                limit=1,
            )
            if not found:
                if not dry_run:
                    product_id = client.create(
                        "product.product",
                        {"name": row.name, "default_code": row.sku, "list_price": row.price},
                    )
                    if stock_location_id is not None:
                        _set_stock(client, product_id, row.qty, stock_location_id)
                result.created.append(row.sku)
                continue

            current = found[0]
            changes: dict = {}
            if current["name"] != row.name:
                changes["name"] = row.name
            if round(current["list_price"], 2) != row.price:
                changes["list_price"] = row.price

            if changes:
                if not dry_run:
                    client.write("product.product", [current["id"]], changes)
                result.updated.append(row.sku)
            else:
                result.unchanged.append(row.sku)

            if stock_location_id is not None and not dry_run:
                _set_stock(client, current["id"], row.qty, stock_location_id)
        except Exception as error:  # keep going: one bad row must not stop the run
            result.errors.append(f"{row.sku}: {error}")
    return result
