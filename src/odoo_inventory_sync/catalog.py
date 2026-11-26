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
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("color", "material", "size")
REQUIRED_COLUMNS = ("model", "color", "material", "size", "price", "cost", "qty")
OPTIONAL_COLUMNS = ("category",)
# Column names as the spreadsheet template shows them, for messages.
COLUMN_NAMES = {"model": "Modelo", "color": "Color", "material": "Material", "size": "Talla",
                "price": "Precio", "cost": "Costo", "qty": "Cantidad", "category": "Categoria"}

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


def loose_key(text: str) -> str:
    """Like key(), but accents do not matter either: 'Café' and 'cafe' match."""
    return strip_accents(key(text))


def accent_clashes(names) -> list[tuple[str, ...]]:
    """Groups of names that differ only by accents, e.g. ('Cafe', 'Café').

    Such names would become two models or two values in Odoo with the same
    code (SKU), so they are refused instead of guessing which one is meant.
    """
    groups: dict[str, list[str]] = {}
    for name in names:
        spellings = groups.setdefault(loose_key(name), [])
        if key(name) not in {key(n) for n in spellings}:
            spellings.append(name)
    return [tuple(g) for g in groups.values() if len(g) > 1]


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
        raise ValueError("un EAN-13 necesita exactamente 12 dígitos antes del dígito de control")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return (10 - total % 10) % 10


def internal_ean13(counter: int) -> str:
    """In-store EAN-13: prefix 2 (reserved for internal use) + 11-digit counter."""
    if not 0 < counter < 10**11:
        raise ValueError("contador de códigos de barras internos fuera de rango")
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
        raise ValueError(f"{what} está vacío")
    if isinstance(value, bool):
        raise ValueError(f"{what} no es un número")
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
            raise ValueError(f"{what} no es un número: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} debe ser un número finito")
    if number < 0:
        raise ValueError(f"{what} no puede ser negativo")
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
        raise CatalogError("para leer .xlsx hace falta openpyxl: pip install openpyxl") from None
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _read_csv(path: Path) -> list[list]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel on Spanish Windows saves "CSV (delimitado por comas)" as cp1252.
        text = raw.decode("cp1252")
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
    raise CatalogError(f"tipo de archivo no admitido {path.suffix!r}: usa .xlsx o .csv")


def file_saved_at(path: str | Path) -> datetime:
    """When the count was written down: the workbook's own "modified" date
    (kept when the file is copied), else the file's modification time. UTC."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True)
        try:
            modified = workbook.properties.modified
        finally:
            workbook.close()
        if modified:
            # openpyxl returns the stored UTC time without a timezone.
            return modified.replace(tzinfo=timezone.utc) if modified.tzinfo is None \
                else modified.astimezone(timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def load_catalog(path: str | Path) -> tuple[list[CatalogRow], list[str]]:
    """Parse the spreadsheet. Returns valid rows and row-level error messages."""
    table = read_table(path)
    # The header is the first row with any content.
    while table and not any(clean(c) for c in table[0]):
        table.pop(0)
    if not table:
        raise CatalogError("el archivo está vacío")
    header_row, data = table[0], table[1:]
    columns: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        name = HEADER_ALIASES.get(normalize_header(cell)) if cell is not None else None
        if name and name not in columns:
            columns[name] = index
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise CatalogError(f"faltan columnas: {', '.join(COLUMN_NAMES[c] for c in missing)}")

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
                    raise ValueError(f"{COLUMN_NAMES[what]} está vacío")
            price = round(parse_number(cells["price"], COLUMN_NAMES["price"]), 2)
            cost = round(parse_number(cells["cost"], COLUMN_NAMES["cost"]), 2)
            qty_value = parse_number(cells["qty"], COLUMN_NAMES["qty"])
            if not qty_value.is_integer():
                raise ValueError(f"Cantidad debe ser un número entero de pares: {cells['qty']!r}")
            identity = (key(model), key(color), key(material), key(size))
            if identity in seen:
                raise ValueError("mismo modelo, color, material y talla que la línea "
                                 f"{seen[identity]}")
        except ValueError as error:
            errors.append(f"línea {line}: {error}")
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
            where = f"línea {row.line}: el modelo {row.model}"
            if row.price != plan.price:
                errors.append(f"{where} tiene precio {row.price}, pero la línea "
                              f"{first_line[model_key]} tiene {plan.price}")
            if row.cost != plan.cost:
                errors.append(f"{where} tiene costo {row.cost}, pero la línea "
                              f"{first_line[model_key]} tiene {plan.cost}")
            if row.category and plan.category and key(row.category) != key(plan.category):
                errors.append(f"{where} tiene categoría {row.category!r}, pero la línea "
                              f"{first_line[model_key]} tiene {plan.category!r}")
            plan.category = plan.category or row.category
        for role in ROLES:
            value = getattr(row, role)
            if key(value) not in {key(v) for v in plan.values[role]}:
                plan.values[role].append(value)
        plan.quantities[(key(row.color), key(row.material), key(row.size))] = row.qty
    for plan in plans.values():
        plan.values["size"].sort(key=size_sort_key)
    for group in accent_clashes(plan.name for plan in plans.values()):
        errors.append(f"los modelos {_quoted(group)} solo se diferencian por tildes; escríbelos igual")
    for role, label in (("color", "colores"), ("material", "materiales"), ("size", "tallas")):
        names = [v for plan in plans.values() for v in plan.values[role]]
        for group in accent_clashes(names):
            errors.append(f"los {label} {_quoted(group)} solo se diferencian por tildes; escríbelos igual")
    return list(plans.values()), errors


def _quoted(names) -> str:
    return " y ".join(repr(n) for n in names)
