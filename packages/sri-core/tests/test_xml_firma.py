import datetime as dt
import shutil
import subprocess

import pytest
from lxml import etree

from sri_core import (
    Comprador, FirmaError, Firmador, Item, NotaCredito, factura_xml, nota_credito_xml, verificar_firma,
)
from sri_core.firma import DS

from conftest import hacer_certificado, hacer_p12, validar_xsd


def test_factura_cumple_xsd_oficial(factura):
    xml = factura_xml(factura)
    validar_xsd(xml, "factura_V1.1.0.xsd")
    doc = etree.fromstring(xml)
    assert doc.findtext("infoTributaria/claveAcceso") == factura.clave_acceso
    assert doc.findtext("infoTributaria/secuencial") == "000000123"
    assert doc.findtext("infoFactura/importeTotal") == "52.50"
    assert doc.findtext("infoFactura/obligadoContabilidad") == "SI"
    assert doc.findtext("infoTributaria/contribuyenteRimpe") == "CONTRIBUYENTE RÉGIMEN RIMPE"
    campos = {c.get("nombre"): c.text for c in doc.iterfind("infoAdicional/campoAdicional")}
    assert campos == {"Email": "ana@correo.com", "Vendedor": "Caja 1"}


def test_factura_consumidor_final_cumple_xsd(emisor):
    from sri_core import Factura
    f = Factura(emisor, 1, Comprador.consumidor_final(), [Item("A", "x", 1, "10", precio_incluye_iva=True)])
    xml = factura_xml(f)
    validar_xsd(xml, "factura_V1.1.0.xsd")
    assert etree.fromstring(xml).findtext("infoFactura/identificacionComprador") == "9999999999999"


def test_nota_credito_cumple_xsd(emisor):
    nc = NotaCredito(
        emisor, 7, Comprador.desde_identificacion("1710034065", "Ana Pérez"),
        [Item("ZAP-38-NEG", "Botín negro talla 38", 1, "45.00", precio_incluye_iva=True)],
        fecha=dt.date(2026, 11, 28), factura_numero="001-001-000000123",
        factura_fecha=dt.date(2026, 11, 27), motivo="Cambio de talla",
    )
    xml = nota_credito_xml(nc)
    validar_xsd(xml, "NotaCredito_V1.1.0.xsd")
    doc = etree.fromstring(xml)
    assert doc.findtext("infoNotaCredito/valorModificacion") == "45.00"
    assert nc.clave_acceso[8:10] == "04"


def test_nota_credito_reglas(emisor):
    base = dict(items=[Item("A", "x", 1, 1)], factura_fecha=dt.date(2026, 1, 1))
    with pytest.raises(ValueError, match="consumidor final"):
        NotaCredito(emisor, 1, Comprador.consumidor_final(), factura_numero="001-001-000000001", **base)
    with pytest.raises(ValueError, match="forma"):
        NotaCredito(emisor, 1, Comprador.desde_identificacion("1710034065", "A"), factura_numero="1-1-1", **base)


def test_texto_con_caracteres_especiales(emisor):
    from sri_core import Factura
    f = Factura(emisor, 2, Comprador.desde_identificacion("1710034065", "Peña & Hijos <S.A.>"),
                [Item("A", 'Zapato "cómodo"\n talla  40', 1, "10", precio_incluye_iva=True)])
    xml = factura_xml(f)
    validar_xsd(xml, "factura_V1.1.0.xsd")
    doc = etree.fromstring(xml)
    assert doc.findtext("infoFactura/razonSocialComprador") == "Peña & Hijos <S.A.>"
    assert doc.findtext("detalles/detalle/descripcion") == 'Zapato "cómodo" talla 40'


def test_firma_y_verificacion(factura, p12):
    firmado = Firmador.desde_p12(p12, "clave123").firmar(factura_xml(factura))
    assert verificar_firma(firmado)
    doc = etree.fromstring(firmado)
    firma = doc.find(f"{{{DS}}}Signature")
    assert firma is not None and doc[-1] is firma  # firma enveloped, al final del comprobante
    assert len(firma.findall(f"{{{DS}}}SignedInfo/{{{DS}}}Reference")) == 3


def test_xml_alterado_no_pasa(factura, p12):
    firmado = Firmador.desde_p12(p12, "clave123").firmar(factura_xml(factura))
    alterado = firmado.replace(b"<importeTotal>52.50<", b"<importeTotal>5.25<")
    assert alterado != firmado
    assert not verificar_firma(alterado)


@pytest.mark.skipif(not shutil.which("xmlsec1"), reason="xmlsec1 no está instalado")
def test_firma_valida_para_xmlsec1(factura, llave_y_cert, tmp_path):
    from cryptography.hazmat.primitives import serialization

    llave, cert = llave_y_cert
    firmado = Firmador(llave, cert).firmar(factura_xml(factura))
    (tmp_path / "f.xml").write_bytes(firmado)
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # El certificado de prueba se marca como confiable: xmlsec1 1.3 no acepta la llave
    # de un X509Certificate sin confianza (KEY-NOT-FOUND) aunque la firma sea correcta.
    r = subprocess.run(
        ["xmlsec1", "--verify", "--id-attr:id", "factura", "--id-attr:Id", "KeyInfo",
         "--id-attr:Id", "SignedProperties", "--trusted-pem", str(tmp_path / "c.pem"), str(tmp_path / "f.xml")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # xmlsec1 falla si cualquiera de las tres referencias no cuadra. La versión 1.2 imprime
    # "OK" y el conteo de referencias; la 1.3 solo "Verification status: OK".
    assert "OK" in r.stderr


def test_p12_contrasena_incorrecta(p12):
    with pytest.raises(FirmaError, match="contraseña"):
        Firmador.desde_p12(p12, "otra")


def test_p12_vencido():
    llave, cert = hacer_certificado(dias=-1, desde=-400)
    with pytest.raises(FirmaError, match="venció"):
        Firmador.desde_p12(hacer_p12(llave, cert), "clave123")
    assert Firmador.desde_p12(hacer_p12(llave, cert), "clave123", permitir_vencido=True)


def test_p12_con_varios_certificados_elige_el_de_firma():
    llave, cert_cifrado = hacer_certificado(uso_firma=False, nombre="CIFRADO")
    _, cert_firma = hacer_certificado(llave=llave, nombre="FIRMA")
    _, ajeno = hacer_certificado(nombre="CA AJENA")
    f = Firmador.desde_p12(hacer_p12(llave, cert_cifrado, extra=[ajeno, cert_firma]), "clave123")
    assert f.info().titular == "FIRMA"


def test_info_del_certificado(p12):
    info = Firmador.desde_p12(p12, "clave123").info()
    assert info.titular == "FIRMA DE PRUEBA"
    assert 360 <= info.dias_para_vencer <= 365


def test_no_firma_dos_veces(factura, p12):
    f = Firmador.desde_p12(p12, "clave123")
    with pytest.raises(FirmaError, match="ya está firmado"):
        f.firmar(f.firmar(factura_xml(factura)))
