from decimal import Decimal

import pytest

from sri_core import Comprador, Factura, Item, Pago, calcular_totales


def test_precio_de_percha_con_iva_cuadra_al_centavo():
    lineas, t = calcular_totales([Item("A", "Botín", 1, "45.00", precio_incluye_iva=True)])
    assert lineas[0].base == Decimal("39.13")
    assert lineas[0].iva == Decimal("5.87")
    assert t.importe_total == Decimal("45.00")


@pytest.mark.parametrize("precio", ["0.99", "1.00", "9.99", "19.90", "45.00", "59.99", "123.45"])
@pytest.mark.parametrize("cantidad", [1, 2, 3, 7])
def test_con_iva_incluido_el_total_es_siempre_el_de_percha(precio, cantidad):
    _, t = calcular_totales([Item("A", "x", cantidad, precio, precio_incluye_iva=True)])
    assert t.importe_total == Decimal(precio) * cantidad
    # El IVA de la línea no se aleja más de un centavo de base × tarifa.
    assert abs(t.total_iva - (t.subtotal_sin_impuestos * Decimal("0.15")).quantize(Decimal("0.01"))) <= Decimal("0.01")


def test_precio_sin_iva_suma_el_iva_encima():
    lineas, t = calcular_totales([Item("A", "x", 2, "10.00", descuento="2.00")])
    assert lineas[0].base == Decimal("18.00")
    assert lineas[0].iva == Decimal("2.70")
    assert t.total_descuento == Decimal("2.00")
    assert t.importe_total == Decimal("20.70")


def test_descuento_con_iva_incluido():
    lineas, t = calcular_totales([Item("A", "x", 1, "45.00", descuento="5.00", precio_incluye_iva=True)])
    assert t.importe_total == Decimal("40.00")
    assert lineas[0].descuento == Decimal("4.35")
    assert lineas[0].precio_unitario * 1 - lineas[0].descuento == pytest.approx(lineas[0].base, abs=Decimal("0.01"))


def test_mezcla_de_tarifas_agrupa_por_codigo():
    _, t = calcular_totales([
        Item("A", "x", 1, "11.50", iva=15, precio_incluye_iva=True),
        Item("B", "y", 1, "5.00", iva=0),
        Item("C", "z", 1, "3.00", iva="no_objeto"),
    ])
    assert {i.codigo_porcentaje for i in t.impuestos} == {"4", "0", "6"}
    assert t.base_por_tarifa("4") == Decimal("10.00")
    assert t.importe_total == Decimal("19.50")


def test_tarifa_desconocida():
    with pytest.raises(ValueError, match="no está en la tabla"):
        Item("A", "x", 1, 1, iva=16)


def test_pagos_deben_cuadrar(emisor):
    with pytest.raises(ValueError, match="pagos suman"):
        Factura(emisor, 1, Comprador.consumidor_final(), [Item("A", "x", 1, "10", precio_incluye_iva=True)],
                pagos=[Pago("efectivo", "9.99")])


def test_pago_por_defecto_es_efectivo_por_el_total(emisor):
    f = Factura(emisor, 1, Comprador.consumidor_final(), [Item("A", "x", 2, "10", precio_incluye_iva=True)])
    assert len(f.pagos) == 1 and f.pagos[0].codigo == "01" and f.pagos[0].total == Decimal("20.00")


def test_limite_consumidor_final(emisor):
    Factura(emisor, 1, Comprador.consumidor_final(), [Item("A", "x", 1, "50.00", precio_incluye_iva=True)])
    with pytest.raises(ValueError, match="consumidor final"):
        Factura(emisor, 2, Comprador.consumidor_final(), [Item("A", "x", 1, "50.01", precio_incluye_iva=True)])


def test_varias_formas_de_pago(emisor):
    f = Factura(emisor, 1, Comprador.desde_identificacion("1710034065", "Ana"),
                [Item("A", "x", 1, "100", precio_incluye_iva=True)],
                pagos=[Pago("efectivo", "40"), Pago("tarjeta_credito", "60")])
    assert [p.codigo for p in f.pagos] == ["01", "19"]


def test_comprador_por_identificacion():
    assert Comprador.desde_identificacion("1710034065", "A").tipo.value == "05"
    assert Comprador.desde_identificacion("1790012345001", "A").tipo.value == "04"
    assert Comprador.desde_identificacion("AB123456", "A").tipo.value == "06"
    assert Comprador.desde_identificacion("9999999999999", "x").es_consumidor_final
    with pytest.raises(ValueError, match="Cédula"):
        Comprador.desde_identificacion("1710034066", "A")
