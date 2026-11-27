import io

import pytest

from sri_core import ClienteSRI, Firmador, SRIConexionError, factura_xml, generar_ride
from sri_core.sri_cliente import RespuestaAutorizacion

from sri_falso import SesionFalsa, autorizacion, devuelta, recibida, sin_autorizaciones


@pytest.fixture
def firmado(factura, p12):
    return Firmador.desde_p12(p12, "clave123").firmar(factura_xml(factura))


def test_flujo_autorizado(factura, firmado):
    s = SesionFalsa([recibida(), autorizacion(factura.clave_acceso)])
    r = ClienteSRI("1", sesion=s).enviar_y_autorizar(firmado, dormir=lambda _: None)
    assert r.autorizado
    assert r.numero_autorizacion == factura.clave_acceso
    assert r.fecha_autorizacion.year == 2026
    assert b"<factura" in r.comprobante_xml
    assert s.llamadas[0][0].startswith("https://celcer.sri.gob.ec/")
    assert b"validarComprobante" in s.llamadas[0][1]
    assert b"<autorizacion>" in r.xml_autorizado()


def test_produccion_usa_cel(firmado, factura):
    s = SesionFalsa([recibida(), autorizacion(factura.clave_acceso)])
    ClienteSRI("2", sesion=s).enviar_y_autorizar(firmado, dormir=lambda _: None)
    assert s.llamadas[0][0].startswith("https://cel.sri.gob.ec/")


def test_devuelta_trae_el_motivo(firmado):
    s = SesionFalsa([devuelta("39", "FIRMA INVALIDA", "La firma es invalida")])
    r = ClienteSRI(sesion=s).enviar_y_autorizar(firmado)
    assert r.estado == "DEVUELTA" and not r.autorizado
    assert str(r.mensajes[0]) == "[39] FIRMA INVALIDA (La firma es invalida)"
    assert len(s.llamadas) == 1


def test_clave_ya_registrada_consulta_autorizacion(firmado, factura):
    s = SesionFalsa([devuelta("43", "CLAVE ACCESO REGISTRADA"), autorizacion(factura.clave_acceso)])
    assert ClienteSRI(sesion=s).enviar_y_autorizar(firmado).autorizado


def test_reintenta_mientras_esta_en_proceso(firmado, factura):
    esperas = []
    s = SesionFalsa([recibida(), sin_autorizaciones(factura.clave_acceso), sin_autorizaciones(factura.clave_acceso),
                     autorizacion(factura.clave_acceso)])
    r = ClienteSRI(sesion=s).enviar_y_autorizar(firmado, espera=2, dormir=esperas.append)
    assert r.autorizado and esperas == [2, 2]


def test_sigue_en_proceso_tras_agotar_intentos(firmado, factura):
    s = SesionFalsa([recibida()] + [sin_autorizaciones(factura.clave_acceso)] * 3)
    r = ClienteSRI(sesion=s).enviar_y_autorizar(firmado, intentos=3, dormir=lambda _: None)
    assert r.estado == "EN PROCESO"


def test_no_autorizado(firmado, factura):
    msg = "<mensaje><identificador>65</identificador><mensaje>FECHA EMISION EXTEMPORANEA</mensaje><tipo>ERROR</tipo></mensaje>"
    s = SesionFalsa([recibida(), autorizacion(factura.clave_acceso, "NO AUTORIZADO", msg)])
    r = ClienteSRI(sesion=s).enviar_y_autorizar(firmado)
    assert r.estado == "NO AUTORIZADO"
    assert r.mensajes[0].identificador == "65"
    with pytest.raises(ValueError):
        r.xml_autorizado()


def test_sin_conexion(firmado):
    import requests
    s = SesionFalsa([requests.ConnectionError("sin red")])
    with pytest.raises(SRIConexionError):
        ClienteSRI(sesion=s).enviar(firmado)


def texto_pdf(pdf):
    from pypdf import PdfReader
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_ride_factura(factura):
    resp = RespuestaAutorizacion("AUTORIZADO", factura.clave_acceso, factura.clave_acceso, None, "PRUEBAS")
    pdf = generar_ride(factura, resp)
    assert pdf.startswith(b"%PDF")
    texto = texto_pdf(pdf)
    for esperado in ["1790012345001", "FACTURA", "001-001-000000123", factura.clave_acceso, "52.50",
                     "SUBTOTAL 15%", "IVA 15%", "Ana Pérez", "PRUEBAS"]:
        assert esperado in texto, esperado


def test_ride_pendiente_y_nota_credito(emisor):
    import datetime as dt
    from sri_core import Comprador, Item, NotaCredito
    nc = NotaCredito(emisor, 7, Comprador.desde_identificacion("1710034065", "Ana"),
                     [Item("A", "Botín", 1, "45", precio_incluye_iva=True)],
                     factura_numero="001-001-000000123", factura_fecha=dt.date(2026, 11, 27), motivo="Cambio de talla")
    texto = texto_pdf(generar_ride(nc))
    assert "NOTA DE CRÉDITO" in texto and "PENDIENTE DE AUTORIZACIÓN" in texto and "Cambio de talla" in texto
