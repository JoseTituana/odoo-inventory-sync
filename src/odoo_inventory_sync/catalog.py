"""Read a footwear catalog spreadsheet and plan it as Odoo product variants.

One spreadsheet row is one variant: model x color x material x size, with
its on-hand quantity. Rows of the same model become one product template
whose variants cover every combination of the colors, materials and sizes
listed for that model (combinations without a row get quantity 0).

This module has no Odoo dependency: it parses, validates and builds codes.
``catalog_sync`` applies a plan to Odoo.
"""
from __future__ import annotations

import csv
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("color", "material", "size")
REQUIRED_COLUMNS = ("model", "color", "material", "size", "price", "cost", "qty")
OPTIONAL_COLUMNS = ("category",)

# Accepted header spellings, compared after normalize_header().
HEADER_ALIASES = {
    "model": "model", "modelo": "model", "name": "model", "nombre": "model",
    "nombre del modelo": "model",
    "color": "color",
    "material": "material",
    "size": "size", "talla": "size",
    "price": "price", "precio": "price", "precio de venta": "price", "pvp": "price",
    "cost": "cost", "costo": "cost", "precio de fabricacion": "cost",
    "costo de fabricacion": "cost",
    "qty": "qty", "quantity": "qty", "cantidad": "qty", "pares": "qty", "stock": "qty",
    "category": "category", "categoria": "category",
}


class CatalogError(ValueError):
    """The spreadsheet cannot be imported as a whole."""


@dataclass(frozen=True)
class CatalogRow:
    line: int
    model: str
    color: str
    material: str
    size: str
    price: float
    cost: float
    qty: int
    category: str = ""


@dataclass
class ModelPlan:
    """Everything the spreadsheet says about one model."""

    name: str
    price: float
    cost: float
    category: str
    # Ordered, de-duplicated display spellings per role (first spelling wins).
    values: dict[str, list[str]] = field(default_factory=dict)
    # (color_key, material_key, size_key) -> quantity
    quantities: dict[tuple[str, str, str], int] = field(default_factory=dict)

    @property
    def variant_count(self) -> int:
        return math.prod(len(self.values[role]) for role in ROLES)

    @property
    def pairs(self) -> int:
        return sum(self.quantities.values())


# ---------------------------------------------------------------- text helpers

def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_header(text: str) -> str:
    text = strip_accents(str(text)).lower().replace("_", " ")
    return " ".join(text.split())


def key(text: str) -> str:
    """Comparison key: case and repeated spaces do not matter, accents do."""
    return " ".join(str(text).split()).casefold()


def clean(text) -> str:
    return " ".join(str(text).split()) if text is not None else ""


def slug(text: str) -> str:
    """ASCII upper-case code: 'Café claro' -> 'CAFE-CLARO'."""
    ascii_text = strip_accents(text).upper()
    return re.sub(r"[^A-Z0-9]+", "-", ascii_text).strip("-")


def abbreviations(names: list[str], length: int = 3) -> dict[str, str]:
    """Short code per value, e.g. NEGRO -> NEG.

    Values whose short code would collide (CAFE / CAFE CLARO) keep their
    full slug, so codes stay unique within the list.
    """
    short = {key(n): slug(n).replace("-", "")[:length] for n in names}
    counts: dict[str, int] = {}
    for code in short.values():
        counts[code] = counts.get(code, 0) + 1
    return {
        key(n): (short[key(n)] if counts[short[key(n)]] == 1 else slug(n))
        for n in names
    }


def build_sku(model: str, color_code: str, material_code: str, size: str) -> str:
    return "-".join([slug(model), color_code, material_code, slug(size)])


def ean13_check_digit(first12: str) -> int:
    if len(first12) != 12 or not first12.isdigit():
        raise ValueError("EAN-13 needs exactly 12 digits before the check digit")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return (10 - total % 10) % 10


def internal_ean13(counter: int) -> str:
    """In-store EAN-13: prefix 2 (reserved for internal use) + 11-digit counter."""
    if not 0 < counter < 10**11:
        raise ValueError("internal barcode counter out of range")
    first12 = f"2{counter:011d}"
    return first12 + str(ean13_check_digit(first12))


def internal_counter(barcode: str | None) -> int | None:
    """Counter of one of our internal barcodes, or None for any other code."""
    if barcode and len(barcode) == 13 and barcode.isdigit() and barcode[0] == "2":
        if ean13_check_digit(barcode[:12]) == int(barcode[12]):
            return int(barcode[1:12])
    return None


def size_sort_key(size: str) -> tuple:
    try:
        return (0, float(size), size)
    except ValueError:
        return (1, 0.0, key(size))


# --------------------------------------------------------------------- parsing

def parse_number(value, what: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{what} is empty")
    if isinstance(value, bool):
        raise ValueError(f"{what} is not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(" ", "").replace("$", "")
        if "," in text and "." in text:  # 1.234,50 -> 1234.50
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{what} is not a number: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number")
    if number < 0:
        raise ValueError(f"{what} must not be negative")
    return number


def parse_size(value) -> str:
    """38, 38.0 and '38' are all size '38'; '38,5' becomes '38.5'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
    text = clean(value)
    if re.fullmatch(r"\d+([.,]\d+)?", text):
        number = float(text.replace(",", "."))
        return str(int(number)) if number.is_integer() else f"{number:g}"
    return text


def _read_xlsx(path: Path) -> list[list]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - depends on the environment
        raise CatalogError("reading .xlsx needs openpyxl: pip install openpyxl") from None
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _read_csv(path: Path) -> list[list]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(text.splitlines(), dialect)]


def read_table(path: str | Path) -> list[list]:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    if path.suffix.lower() in (".csv", ".txt"):
        return _read_csv(path)
    raise CatalogError(f"unsupported file type {path.suffix!r}: use .xlsx or .csv")


def load_catalog(path: str | Path) -> tuple[list[CatalogRow], list[str]]:
    """Parse the spreadsheet. Returns valid rows and row-level error messages."""
    table = read_table(path)
    # The header is the first row with any content.
    while table and not any(clean(c) for c in table[0]):
        table.pop(0)
    if not table:
        raise CatalogError("the file is empty")
    header_row, data = table[0], table[1:]
    columns: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        name = HEADER_ALIASES.get(normalize_header(cell)) if cell is not None else None
        if name and name not in columns:
            columns[name] = index
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise CatalogError(f"missing columns: {', '.join(missing)}")

    rows: list[CatalogRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, ...], int] = {}
    for offset, raw in enumerate(data):
        line = offset + 2
        cells = {name: (raw[i] if i < len(raw) else None) for name, i in columns.items()}
        if not any(clean(v) for v in cells.values()):
            continue  # blank line
        try:
            model, color, material = (clean(cells[c]) for c in ("model", "color", "material"))
            size = parse_size(cells["size"])
            for what, value in (("model", model), ("color", color),
                                ("material", material), ("size", size)):
                if not value:
                    raise ValueError(f"{what} is empty")
            price = round(parse_number(cells["price"], "price"), 2)
            cost = round(parse_number(cells["cost"], "cost"), 2)
            qty_value = parse_number(cells["qty"], "qty")
            if not qty_value.is_integer():
                raise ValueError(f"qty must be a whole number of pairs: {cells['qty']!r}")
            identity = (key(model), key(color), key(material), key(size))
            if identity in seen:
                raise ValueError(f"same model, color, material and size as line {seen[identity]}")
        except ValueError as error:
            errors.append(f"line {line}: {error}")
            continue
        seen[identity] = line
        rows.append(CatalogRow(line, model, color, material, size, price, cost,
                               int(qty_value), clean(cells.get("category"))))
    return rows, errors


# -------------------------------------------------------------------- planning

def build_plans(rows: list[CatalogRow]) -> tuple[list[ModelPlan], list[str]]:
    """Group rows by model and check that per-model fields agree."""
    plans: dict[str, ModelPlan] = {}
    first_line: dict[str, int] = {}
    errors: list[str] = []
    for row in rows:
        model_key = key(row.model)
        plan = plans.get(model_key)
        if plan is None:
            plan = ModelPlan(row.model, row.price, row.cost, row.category,
                             {role: [] for role in ROLES})
            plans[model_key] = plan
            first_line[model_key] = row.line
        else:
            where = f"line {row.line}: model {row.model}"
            if row.price != plan.price:
                errors.append(f"{where} has price {row.price}, but line "
                              f"{first_line[model_key]} has {plan.price}")
            if row.cost != plan.cost:
                errors.append(f"{where} has cost {row.cost}, but line "
                              f"{first_line[model_key]} has {plan.cost}")
            if row.category and plan.category and key(row.category) != key(plan.category):
                errors.append(f"{where} has category {row.category!r}, but line "
                              f"{first_line[model_key]} has {plan.category!r}")
            plan.category = plan.category or row.category
        for role in ROLES:
            value = getattr(row, role)
            if key(value) not in {key(v) for v in plan.values[role]}:
                plan.values[role].append(value)
        plan.quantities[(key(row.color), key(row.material), key(row.size))] = row.qty
    for plan in plans.values():
        plan.values["size"].sort(key=size_sort_key)
    return list(plans.values()), errors
