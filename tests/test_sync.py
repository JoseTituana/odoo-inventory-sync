import pytest

from odoo_inventory_sync.sync import ProductRow, load_rows, sync_products


class FakeClient:
    """In-memory stand-in for OdooClient."""

    def __init__(self, products=None, fail_on_sku=None):
        self.products = products or []
        self.fail_on_sku = fail_on_sku
        self.created = []
        self.written = []
        self.calls = []
        self._next_id = 100

    def search_read(self, model, domain, fields, limit=None):
        sku = domain[0][2]
        if sku == self.fail_on_sku:
            raise RuntimeError("boom")
        return [p for p in self.products if p["default_code"] == sku][:limit]

    def create(self, model, values):
        self._next_id += 1
        self.created.append((model, values))
        return self._next_id

    def write(self, model, ids, values):
        self.written.append((model, ids, values))
        return True

    def call(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args))


def write_csv(tmp_path, text):
    path = tmp_path / "products.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_rows_valid(tmp_path):
    path = write_csv(tmp_path, "sku,name,price,qty\nA1,Shoe,25.5,10\n")
    rows, errors = load_rows(path)
    assert rows == [ProductRow("A1", "Shoe", 25.5, 10.0)]
    assert errors == []


def test_load_rows_reports_bad_lines_and_duplicates(tmp_path):
    path = write_csv(
        tmp_path,
        "sku,name,price,qty\nA1,Shoe,25,1\nA1,Again,25,1\nB2,Boot,abc,1\nC3,Sandal,-5,1\n,NoSku,1,1\n",
    )
    rows, errors = load_rows(path)
    assert [r.sku for r in rows] == ["A1"]
    assert len(errors) == 4
    assert any("repetido" in e for e in errors)


def test_load_rows_reports_short_rows(tmp_path):
    path = write_csv(tmp_path, "sku,name,price,qty\nA1,Shoe,25,1\nB2,Short\n")
    rows, errors = load_rows(path)
    assert [r.sku for r in rows] == ["A1"]
    assert errors == ["línea 3: faltan valores en price, qty"]


@pytest.mark.parametrize(
    "price, qty", [("nan", "1"), ("inf", "1"), ("-inf", "1"), ("25", "nan"), ("25", "inf")]
)
def test_load_rows_rejects_non_finite_numbers(tmp_path, price, qty):
    path = write_csv(tmp_path, f"sku,name,price,qty\nA1,Shoe,{price},{qty}\n")
    rows, errors = load_rows(path)
    assert rows == []
    assert errors == ["línea 2: price y qty deben ser números finitos"]


def test_load_rows_accepts_utf8_bom(tmp_path):
    # Excel on Windows saves "CSV UTF-8" with a byte order mark
    path = tmp_path / "products.csv"
    path.write_text("sku,name,price,qty\nA1,Shoe,25.5,10\n", encoding="utf-8-sig")
    rows, errors = load_rows(path)
    assert rows == [ProductRow("A1", "Shoe", 25.5, 10.0)]
    assert errors == []


def test_load_rows_missing_columns(tmp_path):
    path = write_csv(tmp_path, "sku,name\nA1,Shoe\n")
    with pytest.raises(ValueError, match="faltan columnas"):
        load_rows(path)


def test_creates_new_product():
    client = FakeClient()
    result = sync_products(client, [ProductRow("A1", "Shoe", 25.0, 3)])
    assert result.created == ["A1"]
    assert client.created[0][1]["default_code"] == "A1"


def test_updates_changed_price_only():
    client = FakeClient([{"id": 7, "default_code": "A1", "name": "Shoe", "list_price": 20.0}])
    result = sync_products(client, [ProductRow("A1", "Shoe", 25.0, 3)])
    assert result.updated == ["A1"]
    assert client.written == [("product.product", [7], {"list_price": 25.0})]


def test_unchanged_product_is_not_written():
    client = FakeClient([{"id": 7, "default_code": "A1", "name": "Shoe", "list_price": 25.0}])
    result = sync_products(client, [ProductRow("A1", "Shoe", 25.0, 3)])
    assert result.unchanged == ["A1"]
    assert client.written == [] and client.created == []


def test_dry_run_never_writes():
    client = FakeClient([{"id": 7, "default_code": "A1", "name": "Old", "list_price": 1.0}])
    rows = [ProductRow("A1", "New", 5.0, 1), ProductRow("B2", "Boot", 9.0, 1)]
    result = sync_products(client, rows, dry_run=True, stock_location_id=8)
    assert result.updated == ["A1"] and result.created == ["B2"]
    assert client.written == [] and client.created == [] and client.calls == []


def test_one_failing_row_does_not_stop_the_run():
    client = FakeClient(fail_on_sku="BAD")
    rows = [ProductRow("BAD", "X", 1.0, 1), ProductRow("OK", "Y", 2.0, 1)]
    result = sync_products(client, rows)
    assert result.created == ["OK"]
    assert len(result.errors) == 1 and "BAD" in result.errors[0]


def test_stock_adjustment_applied_when_location_given():
    client = FakeClient()
    sync_products(client, [ProductRow("A1", "Shoe", 25.0, 12)], stock_location_id=8)
    quant_values = client.created[1][1]
    assert quant_values["inventory_quantity"] == 12 and quant_values["location_id"] == 8
    assert client.calls[0][1] == "action_apply_inventory"
