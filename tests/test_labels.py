import pytest

from odoo_inventory_sync.catalog import internal_ean13
from odoo_inventory_sync.labels import (PROFILES, LabelData, Profile, expand, pages_needed,
                                        render_pdf)


def label(pid=1, size="38", color="Negro", barcode=None, model="Marina"):
    return LabelData(pid, model, color, "Cuero", size, 45.0,
                     internal_ean13(pid) if barcode is None else barcode,
                     f"{model.upper()}-{color[:3].upper()}-CUE-{size}")


def test_one_label_per_pair_and_none_for_zero_stock():
    plan = expand([(label(1, "38"), 3), (label(2, "39"), 0), (label(3, "40"), -2)])
    assert len(plan.labels) == 3 and plan.variants == 1
    assert {l.product_id for l in plan.labels} == {1}


def test_labels_are_sorted_by_model_color_and_numeric_size():
    plan = expand([(label(1, "40"), 1), (label(2, "9"), 1), (label(3, "38", color="Miel"), 1)])
    assert [(l.color, l.size) for l in plan.labels] == [("Miel", "38"), ("Negro", "9"),
                                                        ("Negro", "40")]


def test_variant_without_barcode_is_skipped_with_a_warning():
    plan = expand([(label(1, barcode=""), 2), (label(2), 1)])
    assert len(plan.labels) == 1
    assert len(plan.skipped) == 1 and "no barcode" in plan.skipped[0]


def test_fractional_stock_prints_whole_pairs_and_warns():
    plan = expand([(label(1), 2.5)])
    assert len(plan.labels) == 2 and "printing 2" in plan.skipped[0]


@pytest.mark.parametrize("count,start,pages", [(0, 1, 0), (21, 1, 1), (22, 1, 2), (21, 2, 2),
                                               (1, 21, 1), (2, 21, 2)])
def test_pages_needed_on_a4_sheet(count, start, pages):
    assert pages_needed(count, PROFILES["a4-21"], start) == pages


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_builtin_profiles_fit_their_page(name):
    PROFILES[name].check()


def test_slot_positions_run_left_to_right_then_down():
    a4 = PROFILES["a4-21"]
    x0, y0 = a4.position(0)
    assert (x0, round(y0, 2)) == (7.2, round(297 - 15.15 - 38.1, 2))
    x1, y1 = a4.position(1)
    assert x1 == pytest.approx(7.2 + 63.5 + 2.5) and y1 == y0
    x3, y3 = a4.position(3)
    assert x3 == x0 and y3 == pytest.approx(y0 - 38.1)


def test_profile_that_overflows_is_rejected():
    with pytest.raises(ValueError, match="do not fit"):
        Profile("bad", "", 100, 100, 60, 60, 2, 1).check()


def test_render_pdf_counts_pages_and_respects_start_at(tmp_path):
    labels = [label(i) for i in range(1, 6)]
    assert render_pdf(labels, PROFILES["a4-21"], tmp_path / "a.pdf") == 1
    assert render_pdf(labels, PROFILES["a4-21"], tmp_path / "b.pdf", start_at=19) == 2
    assert render_pdf(labels, PROFILES["roll-50x30"], tmp_path / "c.pdf") == 5
    assert (tmp_path / "a.pdf").read_bytes().startswith(b"%PDF")
    with pytest.raises(ValueError):
        render_pdf(labels, PROFILES["a4-21"], tmp_path / "d.pdf", start_at=22)


def test_invalid_barcode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="EAN-13"):
        render_pdf([label(barcode="2000000000010")], PROFILES["a4-21"], tmp_path / "x.pdf")


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_printed_barcode_scans_back_to_the_same_number(tmp_path, name):
    fitz = pytest.importorskip("pymupdf")
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    code = internal_ean13(4242)
    pdf = tmp_path / "scan.pdf"
    render_pdf([label(7, barcode=code)], PROFILES[name], pdf, brand="Calzado Josheps")
    pixmap = fitz.open(pdf)[0].get_pixmap(dpi=203)  # typical thermal printer resolution
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    found = [r.text for r in zxingcpp.read_barcodes(image)]
    assert code in found
