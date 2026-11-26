"""Apply catalog plans to Odoo: templates, variants, SKUs, barcodes, stock.

``import_catalog`` holds the decisions and talks to Odoo only through a
gateway object, so it can be tested with an in-memory fake.
``OdooCatalogGateway`` is the real gateway over ``OdooClient``.

Safety rules:
- Existing SKUs and barcodes are never overwritten (printed labels stay valid).
- A model whose attributes differ from Color, Material and Talla is skipped:
  adding or removing an attribute line makes Odoo delete and recreate variants.
- Archived products with the same name are reported, never duplicated.
- Stock is adjusted only where the counted quantity differs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import OdooError
from .catalog import (ROLES, ModelPlan, abbreviations, build_sku, internal_counter,
                      internal_ean13, key, size_sort_key)

ATTRIBUTE_NAMES = {"color": "Color", "material": "Material", "size": "Talla"}


class CatalogSyncError(Exception):
    """The import cannot start (for example, an attribute is misconfigured)."""


@dataclass
class CatalogResult:
    models_created: list[str] = field(default_factory=list)
    models_updated: list[str] = field(default_factory=list)
    models_unchanged: list[str] = field(default_factory=list)
    attributes_created: list[str] = field(default_factory=list)
    values_created: list[str] = field(default_factory=list)
    categories_created: list[str] = field(default_factory=list)
    variants_created: int = 0
    skus_set: int = 0
    barcodes_set: int = 0
    stock_changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"models: created={len(self.models_created)} updated={len(self.models_updated)} "
            f"unchanged={len(self.models_unchanged)} | variants created={self.variants_created} "
            f"| skus={self.skus_set} barcodes={self.barcodes_set} "
            f"| stock changes={len(self.stock_changes)} | errors={len(self.errors)}"
        )


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards so '=ilike' compares the literal text."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class OdooCatalogGateway:
    """Every Odoo read and write the catalog import needs."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._template_fields: set[str] | None = None

    def _search_read(self, model, domain, fields, limit=None, archived=False):
        kwargs: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kwargs["limit"] = limit
        if archived:
            kwargs["context"] = {"active_test": False}
        return self.client.call(model, "search_read", domain, **kwargs)

    # attributes
    def find_attribute(self, name):
        found = self._search_read("product.attribute", [["name", "=ilike", _escape_like(name)]],
                                  ["id", "create_variant"], limit=1)
        return (found[0]["id"], found[0]["create_variant"]) if found else None

    def create_attribute(self, name):
        return self.client.create("product.attribute", {
            "name": name, "create_variant": "always", "display_type": "pills"})

    def attribute_values(self, attribute_id):
        return self._search_read("product.attribute.value",
                                 [["attribute_id", "=", attribute_id]], ["id", "name"])

    def create_attribute_value(self, attribute_id, name, sequence):
        return self.client.create("product.attribute.value", {
            "attribute_id": attribute_id, "name": name, "sequence": sequence})

    # categories
    def find_category(self, parent_id, name):
        found = self._search_read("product.category", [
            ["name", "=ilike", _escape_like(name)], ["parent_id", "=", parent_id or False]],
            ["id"], limit=1)
        return found[0]["id"] if found else None

    def create_category(self, parent_id, name):
        return self.client.create("product.category", {"name": name, "parent_id": parent_id or False})

    # templates
    def template_fields(self) -> set[str]:
        if self._template_fields is None:
            self._template_fields = set(self.client.call(
                "product.template", "fields_get", attributes=["type"]))
        return self._template_fields

    def find_templates(self, name):
        fields = ["id", "name", "active", "list_price", "categ_id", "product_variant_count"]
        if "available_in_pos" in self.template_fields():  # only once Point of Sale is installed
            fields.append("available_in_pos")
        return self._search_read("product.template", [["name", "=ilike", _escape_like(name)]],
                                 fields, archived=True)

    def create_template(self, values):
        return self.client.create("product.template", values)

    def write_template(self, template_id, values):
        self.client.write("product.template", [template_id], values)

    def template_lines(self, template_id):
        return self._search_read("product.template.attribute.line",
                                 [["product_tmpl_id", "=", template_id]],
                                 ["id", "attribute_id", "value_ids"])

    def add_line_values(self, line_id, value_ids):
        self.client.write("product.template.attribute.line", [line_id],
                          {"value_ids": [(4, v) for v in value_ids]})

    # variants
    def variants(self, template_id):
        """Active variants as dicts with a 'values' set of product.attribute.value ids."""
        ptavs = self._search_read("product.template.attribute.value",
                                  [["product_tmpl_id", "=", template_id]],
                                  ["id", "product_attribute_value_id"])
        to_value = {p["id"]: p["product_attribute_value_id"][0] for p in ptavs}
        rows = self._search_read("product.product", [["product_tmpl_id", "=", template_id]],
                                 ["id", "default_code", "barcode", "standard_price",
                                  "product_template_attribute_value_ids"])
        for row in rows:
            row["values"] = {to_value[p] for p in row["product_template_attribute_value_ids"]
                             if p in to_value}
        return rows

    def write_variant(self, variant_id, values):
        self.client.write("product.product", [variant_id], values)

    def max_internal_counter(self) -> int:
        rows = self._search_read("product.product", [["barcode", "=like", "2" + "_" * 12]],
                                 ["barcode"], archived=True)
        counters = [c for c in (internal_counter(r["barcode"]) for r in rows) if c]
        return max(counters, default=0)

    # stock
    def default_location(self) -> int:
        found = self._search_read("stock.warehouse", [], ["lot_stock_id"], limit=1)
        if not found:
            raise CatalogSyncError("no warehouse found; install Inventory first")
        return found[0]["lot_stock_id"][0]

    def on_hand(self, product_ids, location_id) -> dict[int, float]:
        quants = self._search_read("stock.quant", [["product_id", "in", list(product_ids)],
                                                   ["location_id", "=", location_id]],
                                   ["product_id", "quantity"])
        totals: dict[int, float] = {}
        for quant in quants:
            pid = quant["product_id"][0]
            totals[pid] = totals.get(pid, 0.0) + quant["quantity"]
        return totals

    def set_quantities(self, quantities: dict[int, int], location_id: int) -> None:
        """Inventory adjustment: counted quantity per product, applied in one go."""
        quant_ids = []
        for product_id, qty in quantities.items():
            quant_ids.append(self.client.call(
                "stock.quant", "create",
                {"product_id": product_id, "location_id": location_id,
                 "inventory_quantity": qty},
                context={"inventory_mode": True}))
        if quant_ids:
            try:
                self.client.call("stock.quant", "action_apply_inventory", quant_ids,
                                 context={"inventory_mode": True})
            except OdooError as error:
                # The method returns None, which XML-RPC cannot send back; the
                # adjustment itself is already committed. Anything else is real.
                if "cannot marshal None" not in str(error):
                    raise
            current = self.on_hand(list(quantities), location_id)
            wrong = [pid for pid, qty in quantities.items()
                     if round(current.get(pid, 0.0), 3) != qty]
            if wrong:
                raise CatalogSyncError(f"stock was not applied for product ids {wrong}")


# ------------------------------------------------------------------ the import

def _size_sequence(size: str) -> int:
    kind, number, _ = size_sort_key(size)
    return int(number * 10) if kind == 0 else 10000


def import_catalog(gw, plans: list[ModelPlan], dry_run: bool = False,
                   location_id: int | None = None) -> CatalogResult:
    result = CatalogResult()

    # 1. Attributes must create variants instantly, or stock cannot be tracked per size.
    attribute_ids: dict[str, int | None] = {}
    for role in ROLES:
        name = ATTRIBUTE_NAMES[role]
        found = gw.find_attribute(name)
        if found:
            attribute_id, mode = found
            if mode != "always":
                raise CatalogSyncError(
                    f"attribute {name!r} has Variant Creation = {mode!r}; it must be "
                    f"'Instantly' (always). Change it in Inventory > Configuration > Attributes.")
            attribute_ids[role] = attribute_id
        else:
            attribute_ids[role] = None if dry_run else gw.create_attribute(name)
            result.attributes_created.append(name)

    # 2. Attribute values: reuse case-insensitively, create the missing ones.
    value_ids: dict[str, dict[str, int | None]] = {}
    value_names: dict[int, str] = {}
    for role in ROLES:
        existing = gw.attribute_values(attribute_ids[role]) if attribute_ids[role] else []
        value_ids[role] = {}
        for row in existing:
            value_ids[role].setdefault(key(row["name"]), row["id"])
            value_names[row["id"]] = row["name"]
        wanted: dict[str, str] = {}
        for plan in plans:
            for name in plan.values[role]:
                wanted.setdefault(key(name), name)
        for value_key, name in sorted(wanted.items(), key=lambda kv: size_sort_key(kv[1])):
            if value_key in value_ids[role]:
                continue
            new_id = None
            if not dry_run:
                sequence = _size_sequence(name) if role == "size" else 10
                new_id = gw.create_attribute_value(attribute_ids[role], name, sequence)
                value_names[new_id] = name
            value_ids[role][value_key] = new_id
            result.values_created.append(f"{ATTRIBUTE_NAMES[role]}: {name}")

    # 3. Internal barcodes continue after the highest one already in Odoo.
    counter = gw.max_internal_counter()
    template_fields = gw.template_fields()
    if location_id is None:
        location_id = gw.default_location()

    category_cache: dict[str, int | None] = {}

    def category_id(path: str) -> int | None:
        """Find or create a category path such as 'Calzado / Dama'."""
        if not path:
            return None
        cache_key = key(path)
        if cache_key not in category_cache:
            parent: int | None = None
            parts = [p.strip() for p in path.split("/") if p.strip()]
            missing_from = None
            for index, part in enumerate(parts):
                # In a dry run nothing below a missing category can exist yet.
                found = None if missing_from is not None else gw.find_category(parent, part)
                if found is None:
                    missing_from = index if missing_from is None else missing_from
                    full = " / ".join(parts[:index + 1])
                    if full not in result.categories_created:
                        result.categories_created.append(full)
                    if not dry_run:
                        found = gw.create_category(parent, part)
                parent = found
            category_cache[cache_key] = parent
        return category_cache[cache_key]

    # 4. One model at a time; a failure in one model does not stop the others.
    for plan in plans:
        try:
            if not dry_run:
                # Re-read every time: a model that failed half way may already
                # have used some numbers, and they must never be handed out twice.
                counter = gw.max_internal_counter()
            counter = _import_model(gw, plan, dry_run, location_id, attribute_ids, value_ids,
                                    value_names, counter, template_fields, category_id, result)
        except Exception as error:  # keep going with the next model
            result.errors.append(f"{plan.name}: {error}")
    return result


def _new_template_values(plan, attribute_ids, wanted_ids, template_fields, categ) -> dict:
    values: dict[str, Any] = {
        "name": plan.name, "list_price": plan.price, "sale_ok": True,
        "attribute_line_ids": [
            (0, 0, {"attribute_id": attribute_ids[role], "value_ids": [(6, 0, wanted_ids[role])]})
            for role in ROLES],
    }
    if "is_storable" in template_fields:  # Odoo 18+: goods with tracked inventory
        values.update({"type": "consu", "is_storable": True})
    else:
        values["type"] = "product"
    if "available_in_pos" in template_fields:
        values["available_in_pos"] = True
    if categ:
        values["categ_id"] = categ
    return values


def _import_model(gw, plan, dry_run, location_id, attribute_ids, value_ids, value_names,
                  counter, template_fields, category_id, result) -> int:
    """Create or update one model. Returns the updated barcode counter."""
    display = {role: {key(v): v for v in plan.values[role]} for role in ROLES}

    def show(combination) -> str:
        return "/".join(display[r].get(k, k) for r, k in zip(ROLES, combination))

    wanted_ids = {role: [value_ids[role][key(v)] for v in plan.values[role]] for role in ROLES}
    matches = gw.find_templates(plan.name)
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} products are named like this; rename one in Odoo")
    if matches and not matches[0]["active"]:
        raise ValueError("an archived product has this name; unarchive or rename it first")
    categ = category_id(plan.category)

    # --- new model
    if not matches:
        result.models_created.append(plan.name)
        if dry_run:
            result.variants_created += plan.variant_count
            result.skus_set += plan.variant_count
            result.barcodes_set += plan.variant_count
            result.stock_changes += [f"{plan.name} {show(c)}: 0 -> {q}"
                                     for c, q in plan.quantities.items() if q]
            return counter
        template_id = gw.create_template(
            _new_template_values(plan, attribute_ids, wanted_ids, template_fields, categ))
        before = 0
        changed = True
    # --- existing model
    else:
        template = matches[0]
        template_id = template["id"]
        before = template["product_variant_count"]
        lines = {line["attribute_id"][0]: line for line in gw.template_lines(template_id)}
        if set(lines) != {attribute_ids[role] for role in ROLES}:
            raise ValueError(
                "its attributes are not exactly Color, Material and Talla; adding or removing "
                "an attribute recreates all variants, so fix this product by hand")
        changed = False
        grid = 1
        for role in ROLES:
            line = lines[attribute_ids[role]]
            missing = [v for v in wanted_ids[role] if v is None or v not in line["value_ids"]]
            grid *= len(line["value_ids"]) + len(missing)
            if missing:
                changed = True
                if not dry_run:
                    gw.add_line_values(line["id"], missing)
        if dry_run:
            result.variants_created += max(0, grid - before)
        template_changes: dict[str, Any] = {}
        if round(template["list_price"], 2) != plan.price:
            template_changes["list_price"] = plan.price
        current_categ = template["categ_id"][0] if template["categ_id"] else None
        if categ and categ != current_categ:
            template_changes["categ_id"] = categ
        # A model imported before Point of Sale was installed is not sellable there.
        if "available_in_pos" in template_fields and not template.get("available_in_pos"):
            template_changes["available_in_pos"] = True
        if template_changes:
            changed = True
            if not dry_run:
                gw.write_template(template_id, template_changes)

    variants = gw.variants(template_id)
    if not dry_run:
        result.variants_created += max(0, len(variants) - before)

    # --- SKU, barcode and cost per variant (existing codes are kept)
    lines = {line["attribute_id"][0]: line for line in gw.template_lines(template_id)}
    role_values = {role: set(lines[attribute_ids[role]]["value_ids"]) for role in ROLES}
    codes = {role: abbreviations([value_names[v] for v in lines[attribute_ids[role]]["value_ids"]])
             for role in ("color", "material")}
    by_combination: dict[tuple[str, str, str], dict] = {}
    for variant in variants:
        names = {role: value_names[v] for v in variant["values"]
                 for role in ROLES if v in role_values[role]}
        if len(names) != len(ROLES):
            continue
        combination = (key(names["color"]), key(names["material"]), key(names["size"]))
        by_combination[combination] = variant
        updates: dict[str, Any] = {}
        if not variant["default_code"]:
            updates["default_code"] = build_sku(plan.name, codes["color"][combination[0]],
                                                codes["material"][combination[1]], names["size"])
            result.skus_set += 1
        if not variant["barcode"]:
            counter += 1
            updates["barcode"] = internal_ean13(counter)
            result.barcodes_set += 1
        if round(variant["standard_price"] or 0.0, 2) != plan.cost:
            updates["standard_price"] = plan.cost
        if updates:
            changed = True
            if not dry_run:
                gw.write_variant(variant["id"], updates)

    # --- stock: adjust only where the counted quantity differs
    targets = {by_combination[c]["id"]: q for c, q in plan.quantities.items() if c in by_combination}
    current = gw.on_hand(list(targets), location_id) if targets else {}
    stock_updates = {pid: q for pid, q in targets.items() if round(current.get(pid, 0.0), 3) != q}
    label = {v["id"]: show(c) for c, v in by_combination.items()}
    for pid, qty in stock_updates.items():
        result.stock_changes.append(f"{plan.name} {label[pid]}: {current.get(pid, 0.0):g} -> {qty}")
    if dry_run:  # combinations that do not exist yet
        result.stock_changes += [f"{plan.name} {show(c)}: 0 -> {q}"
                                 for c, q in plan.quantities.items() if c not in by_combination and q]
    elif stock_updates:
        gw.set_quantities(stock_updates, location_id)

    if matches:
        if changed or stock_updates:
            result.models_updated.append(plan.name)
        else:
            result.models_unchanged.append(plan.name)
    return counter
