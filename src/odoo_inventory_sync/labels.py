"""Product labels driven by on-hand stock: one label per pair.

Odoo can print labels for products or for received quantities, but not for
what is on the shelf right now. This module reads stock quantities per
variant, expands them into one label each and renders a PDF for a sheet
of adhesive labels (home printer) or a thermal label roll.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import ean13_check_digit, key, size_sort_key

MM = 72 / 25.4  # PDF points per millimetre

# EAN-13 width in modules, including the left (11) and right (7) quiet zones.
EAN13_MODULES = 95 + 11 + 7
MIN_MODULE_MM = 0.25   # narrower bars are unreliable with cheap scanners
MAX_MODULE_MM = 0.33   # 100 % EAN-13 magnification


@dataclass(frozen=True)
class Profile:
    """Page and label geometry in millimetres."""

    name: str
    description: str
    page_width: float
    page_height: float
    label_width: float
    label_height: float
    columns: int
    rows: int
    margin_left: float = 0.0
    margin_top: float = 0.0
    gap_x: float = 0.0
    gap_y: float = 0.0

    @property
    def per_page(self) -> int:
        return self.columns * self.rows

    def position(self, slot: int) -> tuple[float, float]:
        """Bottom-left corner (mm from the page's bottom-left) of a slot on a page.

        Slots run left to right, then top to bottom, starting at 0.
        """
        row, column = divmod(slot, self.columns)
        x = self.margin_left + column * (self.label_width + self.gap_x)
        top = self.margin_top + row * (self.label_height + self.gap_y)
        return x, self.page_height - top - self.label_height

    def check(self) -> None:
        right = self.margin_left + self.columns * self.label_width + (self.columns - 1) * self.gap_x
        bottom = self.margin_top + self.rows * self.label_height + (self.rows - 1) * self.gap_y
        if right > self.page_width + 0.01 or bottom > self.page_height + 0.01:
            raise ValueError(f"perfil {self.name}: las etiquetas no caben en la página")


PROFILES = {
    p.name: p for p in (
        Profile("a4-21", "Hoja A4, 3 x 7 etiquetas de 63,5 x 38,1 mm (impresora de casa)",
                210, 297, 63.5, 38.1, 3, 7, margin_left=7.2, margin_top=15.15, gap_x=2.5),
        Profile("letter-30", "Hoja Carta, 3 x 10 etiquetas de 66,7 x 25,4 mm (tipo Avery 5160)",
                215.9, 279.4, 66.675, 25.4, 3, 10, margin_left=4.76, margin_top=12.7, gap_x=3.18),
        Profile("roll-50x30", "Rollo térmico, una etiqueta de 50 x 30 mm por página",
                50, 30, 50, 30, 1, 1),
    )
}


@dataclass(frozen=True)
class LabelData:
    product_id: int
    model: str
    color: str
    material: str
    size: str
    price: float
    barcode: str
    sku: str

    def sort_key(self) -> tuple:
        return (key(self.model), key(self.color), key(self.material), size_sort_key(self.size))


@dataclass
class LabelPlan:
    labels: list[LabelData]
    variants: int
    skipped: list[str]

    def summary(self) -> str:
        return (f"{len(self.labels)} etiquetas de {self.variants} variantes"
                + (f", {len(self.skipped)} omitidas" if self.skipped else ""))


# ------------------------------------------------------------------- planning

def expand(stock: list[tuple[LabelData, float]]) -> LabelPlan:
    """One label per whole pair in stock. Zero or negative stock gives none."""
    labels: list[LabelData] = []
    skipped: list[str] = []
    variants = 0
    for data, qty in sorted(stock, key=lambda item: item[0].sort_key()):
        pairs = math.floor(qty + 1e-9)
        if pairs <= 0:
            continue
        name = data.sku or f"{data.model} {data.color} {data.size}"
        if not data.barcode:
            skipped.append(f"{name}: sin código de barras ({pairs} pares)")
            continue
        if qty - pairs > 1e-9:
            skipped.append(f"{name}: {qty:g} en stock, se imprimen {pairs}")
        variants += 1
        labels.extend([data] * pairs)
    return LabelPlan(labels, variants, skipped)


def pages_needed(count: int, profile: Profile, start_at: int = 1) -> int:
    if count <= 0:
        return 0
    return math.ceil((count + start_at - 1) / profile.per_page)


# ------------------------------------------------------------------ rendering

def _fit(canvas, text: str, font: str, size: float, width: float) -> tuple[str, float]:
    """Shrink the font down to 70 % to fit, then cut the text with an ellipsis."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    for scale in (1.0, 0.9, 0.8, 0.7):
        if stringWidth(text, font, size * scale) <= width:
            return text, size * scale
    size *= 0.7
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…", size


def _barcode(value: str, max_width_mm: float, height_mm: float):
    """EAN-13 drawing scaled to the width, or None if it would be unreadable."""
    from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
    from reportlab.graphics.shapes import Drawing

    if len(value) != 13 or not value.isdigit() or ean13_check_digit(value[:12]) != int(value[12]):
        raise ValueError(f"no es un código EAN-13 válido: {value}")
    module = min(MAX_MODULE_MM, max_width_mm / EAN13_MODULES)
    if module < MIN_MODULE_MM:
        return None, module
    font_mm = max(1.8, min(3.0, module * 9))
    widget = Ean13BarcodeWidget(value[:12], barWidth=module * MM,
                                barHeight=max(4.0, height_mm - font_mm) * MM,
                                fontSize=font_mm * MM, humanReadable=True)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(x1 - x0, y1 - y0)
    widget.x, widget.y = -x0, -y0
    drawing.add(widget)
    return drawing, module


def draw_label(canvas, data: LabelData, x: float, y: float, profile: Profile,
               brand: str | None = None) -> None:
    """Draw one label with its bottom-left corner at (x, y) mm."""
    from reportlab.graphics import renderPDF

    w, h = profile.label_width, profile.label_height
    pad = max(1.5, min(3.0, h * 0.07))
    left, right, top = x + pad, x + w - pad, y + h - pad
    inner = right - left
    cursor = top

    def line(text, font, size_mm, align="left", width=inner, at=None):
        nonlocal cursor
        text, size = _fit(canvas, text, font, size_mm * MM, width * MM)
        cursor -= size / MM
        canvas.setFont(font, size)
        px = (at if at is not None else left) * MM
        if align == "center":
            canvas.drawCentredString((left + inner / 2) * MM, cursor * MM, text)
        else:
            canvas.drawString(px, cursor * MM, text)
        cursor -= size / MM * 0.25

    scale = h / 38.1  # sizes are designed for the 38.1 mm A4 label
    if brand:
        line(brand.upper(), "Helvetica-Bold", 2.4 * max(scale, 0.75), align="center")
    line(data.model, "Helvetica-Bold", 3.4 * max(scale, 0.7))

    # Color and material on the left, the SKU in small print on the right.
    detail_mm = 2.6 * max(scale, 0.7)
    detail, detail_size = _fit(canvas, f"{data.color} · {data.material}", "Helvetica",
                               detail_mm * MM, inner * 0.55 * MM)
    cursor -= detail_size / MM
    canvas.setFont("Helvetica", detail_size)
    canvas.drawString(left * MM, cursor * MM, detail)
    if data.sku:
        sku, sku_size = _fit(canvas, data.sku, "Helvetica", detail_mm * 0.75 * MM,
                             inner * 0.43 * MM)
        canvas.setFont("Helvetica", sku_size)
        canvas.drawRightString(right * MM, cursor * MM, sku)
    cursor -= detail_size / MM * 0.35

    # Bottom area: barcode on the left, size above price on the right.
    side = max(15.0, inner * 0.36)
    bottom = y + pad
    area_h = cursor - bottom
    drawing, _module = _barcode(data.barcode, inner - side - 1.0, area_h)
    if drawing is None:
        raise ValueError(f"etiqueta demasiado pequeña para un código de barras legible en el perfil {profile.name}")
    renderPDF.draw(drawing, canvas, left * MM, bottom * MM)

    # Cap heights are about 0.72 of the font size; keep both inside the area.
    size_mm = min(area_h / (0.72 * 1.8 + 0.25), 8.5)
    price_mm = size_mm * 0.8
    price, price_size = _fit(canvas, f"${data.price:,.2f}", "Helvetica-Bold",
                             price_mm * MM, side * MM)
    canvas.setFont("Helvetica-Bold", price_size)
    canvas.drawRightString(right * MM, bottom * MM, price)
    size_text, size_size = _fit(canvas, f"T. {data.size}", "Helvetica-Bold",
                                size_mm * MM, side * MM)
    canvas.setFont("Helvetica-Bold", size_size)
    size_base = bottom + price_size / MM * 0.72 + size_mm * 0.25
    canvas.drawRightString(right * MM, size_base * MM, size_text)


def render_pdf(labels: list[LabelData], profile: Profile, path: str | Path,
               start_at: int = 1, brand: str | None = None, outlines: bool = False) -> int:
    """Write the labels to a PDF. Returns the number of pages."""
    from reportlab.pdfgen.canvas import Canvas

    profile.check()
    if not 1 <= start_at <= profile.per_page:
        raise ValueError(f"start-at debe estar entre 1 y {profile.per_page}")
    canvas = Canvas(str(path), pagesize=(profile.page_width * MM, profile.page_height * MM))
    canvas.setTitle("Etiquetas de productos")
    slot = start_at - 1
    pages = 0
    for data in labels:
        if slot == 0 or pages == 0:
            pages += 1
        x, y = profile.position(slot)
        if outlines:
            canvas.setLineWidth(0.2)
            canvas.setStrokeGray(0.75)
            canvas.rect(x * MM, y * MM, profile.label_width * MM, profile.label_height * MM)
            canvas.setStrokeGray(0)
        draw_label(canvas, data, x, y, profile, brand)
        slot += 1
        if slot == profile.per_page:
            canvas.showPage()
            slot = 0
    if slot != 0:
        canvas.showPage()
    canvas.save()
    return pages


# ------------------------------------------------------------------- Odoo side

ROLE_NAMES = {"color": "color", "material": "material", "talla": "size", "size": "size"}


def read_stock_labels(client: Any, location_id: int | None = None, model: str | None = None,
                      category: str | None = None) -> list[tuple[LabelData, float]]:
    """On-hand quantity per variant in a stock location and its sub-locations."""
    if location_id is None:
        warehouses = client.search_read("stock.warehouse", [], ["lot_stock_id"], limit=1)
        if not warehouses:
            raise ValueError("no hay ninguna bodega")
        location_id = warehouses[0]["lot_stock_id"][0]
    domain: list = [["location_id", "child_of", location_id], ["quantity", ">", 0]]
    if model:
        domain.append(["product_id.product_tmpl_id.name", "ilike", model])
    if category:
        domain.append(["product_id.categ_id.complete_name", "ilike", category])
    quants = client.search_read("stock.quant", domain, ["product_id", "quantity"])
    quantities: dict[int, float] = {}
    for quant in quants:
        pid = quant["product_id"][0]
        quantities[pid] = quantities.get(pid, 0.0) + quant["quantity"]
    if not quantities:
        return []
    products = client.call("product.product", "read", list(quantities),
                           fields=["product_tmpl_id", "default_code", "barcode", "lst_price",
                                   "product_template_attribute_value_ids"])
    ptav_ids = sorted({i for p in products for i in p["product_template_attribute_value_ids"]})
    ptavs = client.call("product.template.attribute.value", "read", ptav_ids,
                        fields=["attribute_id", "name"]) if ptav_ids else []
    by_id = {p["id"]: p for p in ptavs}
    result = []
    for product in products:
        roles = {"color": "", "material": "", "size": ""}
        for ptav_id in product["product_template_attribute_value_ids"]:
            ptav = by_id.get(ptav_id)
            if ptav:
                role = ROLE_NAMES.get(key(ptav["attribute_id"][1]))
                if role:
                    roles[role] = ptav["name"]
        data = LabelData(product["id"], product["product_tmpl_id"][1], roles["color"],
                         roles["material"], roles["size"], float(product["lst_price"]),
                         product["barcode"] or "", product["default_code"] or "")
        result.append((data, quantities[product["id"]]))
    return result
