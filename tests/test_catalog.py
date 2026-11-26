import itertools

import pytest
from openpyxl import Workbook

from odoo_inventory_sync.catalog import (CatalogError, abbreviations, build_plans, build_sku,
                                         ean13_check_digit, internal_counter, internal_ean13,
                                         load_catalog, parse_number, parse_size)
from odoo_inventory_sync.catalog_sync import CatalogSyncError, import_catalog

HEADER = ["Modelo", "Color", "Material", "Talla", "Precio", "Costo", "Cantidad", "Categoria"]


def write_xlsx(tmp_path, rows, header=HEADER):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "catalog.xlsx"
    workbook.save(path)
    return path


# ------------------------------------------------------------------ pure helpers

def test_ean13_check_digit_matches_known_code():
    assert ean13_check_digit("400638133393") == 1  # 4006381333931 is a valid EAN-13


def test_internal_ean13_round_trip():
    code = internal_ean13(42)
    assert code.startswith("2") and len(code) == 13
    assert internal_counter(code) == 42
    assert internal_counter("4006381333931") is None  # not ours
    assert internal_counter(code[:12] + "0" if code[12] != "0" else code[:12] + "1") is None


def test_abbreviations_keep_codes_unique():
    codes = abbreviations(["Negro", "Café", "Café claro", "Miel"])
    assert codes["negro"] == "NEG" and codes["miel"] == "MIE"
    assert codes["café"] != codes["café claro"]


def test_build_sku():
    assert build_sku("Marina Clásica", "NEG", "CUE", "38") == "MARINA-CLASICA-NEG-CUE-38"


@pytest.mark.parametrize("raw,expected", [(38, "38"), (38.0, "38"), ("38", "38"),
                                          ("38,5", "38.5"), (38.5, "38.5"), (" XL ", "XL")])
def test_parse_size(raw, expected):
    assert parse_size(raw) == expected


@pytest.mark.parametrize("raw,expected", [(45, 45.0), ("45,50", 45.5), ("$1.234,50", 1234.5)])
def test_parse_number_accepts_local_formats(raw, expected):
    assert parse_number(raw, "price") == expected


@pytest.mark.parametrize("raw", ["", None, "abc", "-1", "nan"])
def test_parse_number_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        parse_number(raw, "price")


# ------------------------------------------------------------------ reading files

def test_load_catalog_reads_xlsx_with_spanish_headers(tmp_path):
    path = write_xlsx(tmp_path, [["Marina", "Negro", "Cuero", 38, 45, 28, 5, "Calzado / Dama"]])
    rows, errors = load_catalog(path)
    assert errors == []
    assert rows[0].model == "Marina" and rows[0].size == "38" and rows[0].qty == 5
    assert rows[0].category == "Calzado / Dama"


def test_load_catalog_reads_semicolon_csv_with_decimal_commas(tmp_path):
    path = tmp_path / "catalog.csv"
    path.write_text("modelo;color;material;talla;precio;costo;cantidad\n"
                    "Marina;Negro;Cuero;38;45,50;28;5\n", encoding="utf-8-sig")
    rows, errors = load_catalog(path)
    assert errors == [] and rows[0].price == 45.5


def test_load_catalog_reports_row_errors(tmp_path):
    path = write_xlsx(tmp_path, [
        ["Marina", "Negro", "Cuero", 38, 45, 28, 5, ""],
        ["marina", "NEGRO", "cuero", 38, 45, 28, 2, ""],   # same variant, other case
        ["Marina", "", "Cuero", 39, 45, 28, 1, ""],        # no color
        ["Marina", "Negro", "Cuero", 40, 45, 28, 1.5, ""],  # half a pair
    ])
    rows, errors = load_catalog(path)
    assert len(rows) == 1
    assert len(errors) == 3
    assert "line 2" in errors[0] and "line 3" in errors[0]


def test_load_catalog_requires_columns(tmp_path):
    path = write_xlsx(tmp_path, [["Marina"]], header=["Modelo"])
    with pytest.raises(CatalogError, match="missing columns"):
        load_catalog(path)


def test_build_plans_groups_case_insensitively_and_checks_price(tmp_path):
    path = write_xlsx(tmp_path, [
        ["Marina", "Negro", "Cuero", 38, 45, 28, 5, ""],
        ["MARINA", "miel", "Cuero", 36, 45, 28, 2, ""],
        ["Marina", "Negro", "Cuero", 40, 50, 28, 1, ""],
    ])
    rows, _ = load_catalog(path)
    plans, errors = build_plans(rows)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.values["color"] == ["Negro", "miel"]
    assert plan.values["size"] == ["36", "38", "40"]
    assert plan.variant_count == 2 * 1 * 3 and plan.pairs == 8
    assert len(errors) == 1 and "price" in errors[0]


# ------------------------------------------------------------- import with fake Odoo

class FakeGateway:
    """In-memory Odoo that creates the full variant grid like Odoo does."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.attributes = {}   # id -> {name, mode}
        self.values = {}       # id -> {attribute_id, name}
        self.categories = {}   # id -> (parent, name)
        self.templates = {}    # id -> dict
        self.lines = {}        # id -> {template, attribute_id, value_ids}
        self.products = {}     # id -> dict
        self.stock = {}        # (product, location) -> qty
        self.writes = []

    # helpers for tests
    def add_attribute(self, name, mode="always"):
        attribute_id = next(self.ids)
        self.attributes[attribute_id] = {"name": name, "mode": mode}
        return attribute_id

    def _regrid(self, template_id):
        lines = [l for l in self.lines.values() if l["template"] == template_id]
        existing = {frozenset(v["values"]) for v in self.products.values()
                    if v["template"] == template_id}
        for combo in itertools.product(*[l["value_ids"] for l in lines]):
            if frozenset(combo) not in existing:
                self.products[next(self.ids)] = {"template": template_id, "values": set(combo),
                                                 "default_code": False, "barcode": False,
                                                 "standard_price": 0.0}

    # gateway API
    def find_attribute(self, name):
        for i, a in self.attributes.items():
            if a["name"].lower() == name.lower():
                return i, a["mode"]
        return None

    def create_attribute(self, name):
        return self.add_attribute(name)

    def attribute_values(self, attribute_id):
        return [{"id": i, "name": v["name"]} for i, v in self.values.items()
                if v["attribute_id"] == attribute_id]

    def create_attribute_value(self, attribute_id, name, sequence):
        value_id = next(self.ids)
        self.values[value_id] = {"attribute_id": attribute_id, "name": name}
        return value_id

    def find_category(self, parent, name):
        for i, (p, n) in self.categories.items():
            if p == parent and n.lower() == name.lower():
                return i
        return None

    def create_category(self, parent, name):
        category_id = next(self.ids)
        self.categories[category_id] = (parent, name)
        return category_id

    def template_fields(self):
        return {"is_storable", "available_in_pos"}

    def find_templates(self, name):
        return [dict(t, id=i, product_variant_count=sum(
            1 for v in self.products.values() if v["template"] == i))
            for i, t in self.templates.items() if t["name"].lower() == name.lower()]

    def create_template(self, values):
        template_id = next(self.ids)
        self.templates[template_id] = {
            "name": values["name"], "active": True, "list_price": values["list_price"],
            "available_in_pos": values.get("available_in_pos", False),
            "categ_id": [values["categ_id"], "x"] if values.get("categ_id") else False}
        for _, _, line in values["attribute_line_ids"]:
            self.lines[next(self.ids)] = {"template": template_id,
                                          "attribute_id": line["attribute_id"],
                                          "value_ids": list(line["value_ids"][0][2])}
        self._regrid(template_id)
        return template_id

    def write_template(self, template_id, values):
        self.writes.append(("template", template_id, values))
        self.templates[template_id].update(values)

    def template_lines(self, template_id):
        return [{"id": i, "attribute_id": [l["attribute_id"], "x"], "value_ids": list(l["value_ids"])}
                for i, l in self.lines.items() if l["template"] == template_id]

    def add_line_values(self, line_id, value_ids):
        self.lines[line_id]["value_ids"] += value_ids
        self._regrid(self.lines[line_id]["template"])

    def variants(self, template_id):
        return [dict(v, id=i, values=set(v["values"])) for i, v in self.products.items()
                if v["template"] == template_id]

    def write_variant(self, variant_id, values):
        self.writes.append(("variant", variant_id, values))
        self.products[variant_id].update(values)

    def max_internal_counter(self):
        counters = [internal_counter(v["barcode"]) for v in self.products.values()]
        return max([c for c in counters if c], default=0)

    def default_location(self):
        return 8

    def on_hand(self, product_ids, location_id):
        return {p: self.stock[(p, location_id)] for p in product_ids if (p, location_id) in self.stock}

    def set_quantities(self, quantities, location_id):
        self.writes.append(("stock", dict(quantities)))
        for product_id, qty in quantities.items():
            self.stock[(product_id, location_id)] = qty


def plans_from(tmp_path, rows):
    loaded, errors = load_catalog(write_xlsx(tmp_path, rows))
    assert errors == []
    plans, errors = build_plans(loaded)
    assert errors == []
    return plans


MARINA = [["Marina", "Negro", "Cuero", 38, 45, 28, 5, "Calzado / Dama"],
          ["Marina", "Negro", "Cuero", 39, 45, 28, 3, "Calzado / Dama"],
          ["Marina", "Miel", "Cuero", 38, 45, 28, 0, "Calzado / Dama"]]


def test_import_creates_model_grid_codes_and_stock(tmp_path):
    gw = FakeGateway()
    result = import_catalog(gw, plans_from(tmp_path, MARINA))
    assert result.errors == []
    assert result.models_created == ["Marina"]
    assert result.variants_created == 4          # 2 colors x 1 material x 2 sizes
    variants = list(gw.products.values())
    assert len(variants) == 4
    assert all(v["barcode"] and internal_counter(v["barcode"]) for v in variants)
    assert len({v["barcode"] for v in variants}) == 4
    assert {v["default_code"] for v in variants} >= {"MARINA-NEG-CUE-38", "MARINA-MIE-CUE-39"}
    assert sorted(gw.stock.values()) == [3, 5]   # zero quantities are not written
    assert [n for _, n in gw.categories.values()] == ["Calzado", "Dama"]


def test_second_import_changes_nothing(tmp_path):
    gw = FakeGateway()
    import_catalog(gw, plans_from(tmp_path, MARINA))
    gw.writes.clear()
    result = import_catalog(gw, plans_from(tmp_path, MARINA))
    assert result.models_unchanged == ["Marina"]
    assert result.variants_created == 0 and result.stock_changes == []
    assert gw.writes == []


def test_reimport_keeps_barcodes_and_adds_new_size(tmp_path):
    gw = FakeGateway()
    import_catalog(gw, plans_from(tmp_path, MARINA))
    before = {i: v["barcode"] for i, v in gw.products.items()}
    result = import_catalog(gw, plans_from(tmp_path, MARINA + [
        ["Marina", "Negro", "Cuero", 40, 45, 28, 2, "Calzado / Dama"]]))
    assert result.models_updated == ["Marina"]
    assert result.variants_created == 2           # size 40 in both colors
    for variant_id, barcode in before.items():
        assert gw.products[variant_id]["barcode"] == barcode
    assert len({v["barcode"] for v in gw.products.values()}) == 6


def test_only_changed_stock_is_adjusted(tmp_path):
    gw = FakeGateway()
    import_catalog(gw, plans_from(tmp_path, MARINA))
    gw.writes.clear()
    changed = [list(r) for r in MARINA]
    changed[0][6] = 4                             # Negro 38: 5 -> 4
    result = import_catalog(gw, plans_from(tmp_path, changed))
    assert len(result.stock_changes) == 1 and "5 -> 4" in result.stock_changes[0]
    assert gw.writes == [("stock", {next(i for i, v in gw.products.items()
                                         if v["default_code"] == "MARINA-NEG-CUE-38"): 4})]


def test_dry_run_writes_nothing(tmp_path):
    gw = FakeGateway()
    result = import_catalog(gw, plans_from(tmp_path, MARINA), dry_run=True)
    assert result.models_created == ["Marina"] and result.variants_created == 4
    assert len(result.stock_changes) == 2
    assert gw.templates == {} and gw.attributes == {} and gw.writes == []


def test_attribute_that_never_creates_variants_stops_the_import(tmp_path):
    gw = FakeGateway()
    gw.add_attribute("Talla", mode="no_variant")
    with pytest.raises(CatalogSyncError, match="Talla"):
        import_catalog(gw, plans_from(tmp_path, MARINA))
    assert gw.templates == {}


def test_model_with_other_attributes_is_skipped_not_recreated(tmp_path):
    gw = FakeGateway()
    color = gw.add_attribute("Color")
    gw.add_attribute("Material")
    gw.add_attribute("Talla")
    negro = gw.create_attribute_value(color, "Negro", 10)
    gw.create_template({"name": "Marina", "list_price": 45, "attribute_line_ids": [
        (0, 0, {"attribute_id": color, "value_ids": [(6, 0, [negro])]})]})
    variants_before = dict(gw.products)
    result = import_catalog(gw, plans_from(tmp_path, MARINA))
    assert len(result.errors) == 1 and "attributes" in result.errors[0]
    assert gw.products == variants_before


def test_archived_product_with_same_name_is_reported(tmp_path):
    gw = FakeGateway()
    import_catalog(gw, plans_from(tmp_path, MARINA))
    next(iter(gw.templates.values()))["active"] = False
    result = import_catalog(gw, plans_from(tmp_path, MARINA))
    assert len(result.errors) == 1 and "archived" in result.errors[0]
    assert len(gw.templates) == 1


def test_failed_model_does_not_make_the_next_one_reuse_barcodes(tmp_path):
    gw = FakeGateway()
    calls = {"n": 0}
    original = gw.set_quantities

    def fail_first(quantities, location_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("stock failed")
        original(quantities, location_id)

    gw.set_quantities = fail_first
    rows = MARINA + [["Escolar", "Negro", "Cuero", 30, 32, 19, 4, "Calzado / Nino"]]
    result = import_catalog(gw, plans_from(tmp_path, rows))
    assert len(result.errors) == 1 and result.errors[0].startswith("Marina")
    barcodes = [v["barcode"] for v in gw.products.values()]
    assert all(barcodes) and len(set(barcodes)) == len(barcodes)


def test_existing_model_is_made_available_in_pos(tmp_path):
    gw = FakeGateway()
    import_catalog(gw, plans_from(tmp_path, MARINA))
    template = next(iter(gw.templates.values()))
    template["available_in_pos"] = False          # imported before POS was installed
    result = import_catalog(gw, plans_from(tmp_path, MARINA))
    assert result.models_updated == ["Marina"]
    assert template["available_in_pos"] is True
