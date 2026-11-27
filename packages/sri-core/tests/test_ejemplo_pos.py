"""El ejemplo de integración (ejemplos/pos_minimo.py) también se prueba: si la librería
cambia y el ejemplo deja de funcionar, la guía estaría mintiendo."""
import sqlite3
import sys
from pathlib import Path

import pytest

from sri_core import ClienteSRI, Comprador, Firmador, Item, Pago

from sri_falso import SRIQueAutoriza

sys.path.insert(0, str(Path(__file__).parent.parent / "ejemplos"))
from pos_minimo import FacturadorPOS  # noqa: E402


@pytest.fixture
def pos(emisor, p12):
    sesion = SRIQueAutoriza()
    facturador = FacturadorPOS(sqlite3.connect(":memory:"), emisor, Firmador.desde_p12(p12, "clave123"),
                               ClienteSRI(sesion=sesion))
    return facturador, sesion


def plato():
    return [Item("ALM-01", "Almuerzo ejecutivo", 2, "3.50", precio_incluye_iva=True)]


def test_factura_autorizada_con_pdf(pos):
    facturador, _ = pos
    r = facturador.facturar("mesa-4-001", Comprador.desde_identificacion("1710034065", "Ana"), plato())
    assert r["estado"] == "AUTORIZADO" and r["tiene_pdf"]
    assert r["numero"] == "001-001-000000001"
    assert str(r["total"]) == "7.00"


def test_secuencial_sin_saltos(pos):
    facturador, _ = pos
    numeros = [facturador.facturar(f"v{i}", Comprador.consumidor_final(), plato())["numero"] for i in range(3)]
    assert numeros == ["001-001-000000001", "001-001-000000002", "001-001-000000003"]


def test_doble_clic_no_crea_otra_factura(pos):
    facturador, sesion = pos
    a = facturador.facturar("v1", Comprador.consumidor_final(), plato())
    b = facturador.facturar("v1", Comprador.consumidor_final(), plato())
    assert a == b
    assert len(sesion.llamadas) == 2  # un envío y una consulta, no dos facturas


def test_sin_internet_queda_pendiente_y_se_reintenta_con_la_misma_clave(pos):
    facturador, sesion = pos
    sesion.caido = True
    r = facturador.facturar("v1", Comprador.desde_identificacion("1710034065", "Ana"), plato(),
                            pagos=[Pago("tarjeta_debito", "7.00")])
    assert r["estado"] == "PENDIENTE" and not r["tiene_pdf"]
    sesion.caido = False
    assert facturador.reintentar_pendientes() == 1
    despues = facturador.estado(r["clave_acceso"])
    assert despues["estado"] == "AUTORIZADO" and despues["tiene_pdf"]
    assert sesion.ultima_clave == r["clave_acceso"]
    assert facturador.reintentar_pendientes() == 0
