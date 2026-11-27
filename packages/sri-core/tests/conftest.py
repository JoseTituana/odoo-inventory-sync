import datetime as dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from sri_core import Comprador, Emisor, Factura, Item

XSD = Path(__file__).parent / "xsd"


def hacer_certificado(dias=365, desde=-1, uso_firma=True, llave=None, nombre="FIRMA DE PRUEBA"):
    llave = llave or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(n)
        .issuer_name(n)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora + dt.timedelta(days=desde))
        .not_valid_after(ahora + dt.timedelta(days=dias))
        .add_extension(
            x509.KeyUsage(
                digital_signature=uso_firma, content_commitment=False, key_encipherment=not uso_firma,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(llave, hashes.SHA256())
    )
    return llave, cert


def hacer_p12(llave, cert, extra=None, clave=b"clave123"):
    return pkcs12.serialize_key_and_certificates(
        b"prueba", llave, cert, extra, serialization.BestAvailableEncryption(clave)
    )


@pytest.fixture(scope="session")
def llave_y_cert():
    return hacer_certificado()


@pytest.fixture(scope="session")
def p12(llave_y_cert):
    return hacer_p12(*llave_y_cert)


@pytest.fixture
def emisor():
    return Emisor(
        ruc="1790012345001",
        razon_social="CALZADO DE PRUEBA S.A.",
        nombre_comercial="Calzado Prueba",
        dir_matriz="Av. Amazonas N10-20, Quito",
        dir_establecimiento="Av. Amazonas N10-20, Quito",
        obligado_contabilidad=True,
        contribuyente_rimpe="CONTRIBUYENTE RÉGIMEN RIMPE",
    )


@pytest.fixture
def factura(emisor):
    return Factura(
        emisor,
        secuencial=123,
        comprador=Comprador.desde_identificacion("1710034065", "Ana Pérez", email="ana@correo.com"),
        items=[
            Item("ZAP-38-NEG", "Botín negro talla 38", 1, "45.00", precio_incluye_iva=True),
            Item("MED-01", "Medias de algodón", 3, "2.50", iva=0, precio_incluye_iva=True),
        ],
        fecha=dt.date(2026, 11, 27),
        info_adicional={"Vendedor": "Caja 1"},
    )


def validar_xsd(xml: bytes, nombre: str) -> None:
    esquema = etree.XMLSchema(etree.parse(str(XSD / nombre)))
    doc = etree.fromstring(xml)
    assert esquema.validate(doc), esquema.error_log
