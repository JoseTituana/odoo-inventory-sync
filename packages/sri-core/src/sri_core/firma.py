"""Firma XAdES-BES del comprobante con el archivo .p12 del negocio.

Sigue lo que pide la ficha técnica del SRI: firma ENVELOPED dentro del
propio XML, canonicalización C14N inclusiva, RSA-SHA1 y digest SHA1, con
tres referencias (propiedades firmadas, certificado y el comprobante).
Es la misma estructura que produce el módulo OCA l10n_ec_account_edi.

Regla de oro: después de firmar no se toca el XML (ni para darle formato),
porque cualquier espacio cambia el digest y el SRI responde "FIRMA INVALIDA".
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtensionOID, NameOID
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
ETSI = "http://uri.etsi.org/01903/v1.3.2#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
TIPO_SIGNED_PROPERTIES = "http://uri.etsi.org/01903#SignedProperties"


class FirmaError(Exception):
    """Problema con el .p12 o con la firma (contraseña, caducidad, formato)."""


def _ds(tag: str) -> str:
    return f"{{{DS}}}{tag}"


def _etsi(tag: str) -> str:
    return f"{{{ETSI}}}{tag}"


def _c14n(el: etree._Element) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


def _sha1_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode()  # noqa: S324 - lo exige el SRI


def _b64_entero(n: int) -> str:
    return base64.b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode()


def _usa_firma_digital(cert: x509.Certificate) -> bool:
    try:
        return cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value.digital_signature
    except x509.ExtensionNotFound:
        return True


@dataclass
class InfoCertificado:
    titular: str
    emisor: str
    serie: int
    valido_desde: datetime
    valido_hasta: datetime

    @property
    def dias_para_vencer(self) -> int:
        return (self.valido_hasta - datetime.now(timezone.utc)).days


class Firmador:
    """Firma comprobantes con una llave privada RSA y su certificado.

    Uso normal::

        firmador = Firmador.desde_p12("firma.p12", "contraseña")
        xml_firmado = firmador.firmar(xml)
    """

    def __init__(self, clave_privada: rsa.RSAPrivateKey, certificado: x509.Certificate):
        if not isinstance(clave_privada, rsa.RSAPrivateKey):
            raise FirmaError("El SRI solo acepta firmas con llave RSA")
        self.clave_privada = clave_privada
        self.certificado = certificado

    @classmethod
    def desde_p12(cls, p12: bytes | str | Path, clave: str, permitir_vencido: bool = False) -> Firmador:
        """Abre el .p12 (ruta o bytes). Elige el certificado que corresponde a la llave."""
        datos = p12 if isinstance(p12, bytes) else Path(p12).read_bytes()
        try:
            llave, cert, extra = pkcs12.load_key_and_certificates(datos, clave.encode())
        except ValueError as exc:
            raise FirmaError("No se pudo abrir el .p12: revisa la contraseña o el archivo") from exc
        if llave is None:
            raise FirmaError("El .p12 no trae la llave privada")
        candidatos = [c for c in [cert, *(extra or [])] if c is not None]
        pub = llave.public_key().public_numbers()
        propios = [c for c in candidatos if c.public_key().public_numbers() == pub]
        # Algunos .p12 de entidades ecuatorianas traen varios certificados:
        # se toma el de la llave que además permita firma digital.
        elegido = next((c for c in propios if _usa_firma_digital(c)), propios[0] if propios else None)
        if elegido is None:
            raise FirmaError("Ningún certificado del .p12 corresponde a su llave privada")
        firmador = cls(llave, elegido)
        if not permitir_vencido and firmador.info().dias_para_vencer < 0:
            raise FirmaError(f"La firma venció el {firmador.info().valido_hasta:%d/%m/%Y}")
        return firmador

    def info(self) -> InfoCertificado:
        c = self.certificado
        cn = c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return InfoCertificado(
            titular=cn[0].value if cn else c.subject.rfc4514_string(),
            emisor=c.issuer.rfc4514_string(),
            serie=c.serial_number,
            valido_desde=c.not_valid_before_utc,
            valido_hasta=c.not_valid_after_utc,
        )

    def firmar(self, xml: bytes, momento: datetime | None = None) -> bytes:
        """Devuelve el XML con la firma XAdES-BES dentro (bytes UTF-8)."""
        doc = etree.fromstring(xml, etree.XMLParser(remove_blank_text=True))
        if doc.get("id") != "comprobante":
            raise FirmaError('La raíz del comprobante debe tener id="comprobante"')
        if doc.find(_ds("Signature")) is not None:
            raise FirmaError("El comprobante ya está firmado")

        n = lambda: secrets.randbelow(900000) + 100000  # noqa: E731
        sig_id = f"Signature{n()}"
        props_id = f"{sig_id}-SignedProperties{n()}"
        cert_id = f"Certificate{n()}"
        ref_id = f"Reference-ID-{n()}"
        momento = momento or datetime.now(timezone.utc).astimezone()

        # 1) Digest del comprobante, antes de meter la firma (transformación "enveloped").
        digest_comprobante = _sha1_b64(_c14n(doc))

        cert_der = self.certificado.public_bytes(serialization.Encoding.DER)
        numeros = self.certificado.public_key().public_numbers()

        firma = etree.SubElement(doc, _ds("Signature"), nsmap={"ds": DS}, Id=sig_id)
        signed_info = etree.SubElement(firma, _ds("SignedInfo"))
        etree.SubElement(signed_info, _ds("CanonicalizationMethod"), Algorithm=C14N)
        etree.SubElement(signed_info, _ds("SignatureMethod"), Algorithm=RSA_SHA1)

        def referencia(uri: str, **attrs) -> etree._Element:
            ref = etree.SubElement(signed_info, _ds("Reference"), **attrs, URI=uri)
            return ref

        ref_props = referencia(f"#{props_id}", Id=f"SignedPropertiesID{n()}", Type=TIPO_SIGNED_PROPERTIES)
        ref_cert = referencia(f"#{cert_id}")
        ref_comp = referencia("#comprobante", Id=ref_id)
        transforms = etree.SubElement(ref_comp, _ds("Transforms"))
        etree.SubElement(transforms, _ds("Transform"), Algorithm=ENVELOPED)
        for ref in (ref_props, ref_cert, ref_comp):
            etree.SubElement(ref, _ds("DigestMethod"), Algorithm=SHA1)
            etree.SubElement(ref, _ds("DigestValue"))
        ref_comp.find(_ds("DigestValue")).text = digest_comprobante

        valor_firma = etree.SubElement(firma, _ds("SignatureValue"))

        key_info = etree.SubElement(firma, _ds("KeyInfo"), Id=cert_id)
        x509_data = etree.SubElement(key_info, _ds("X509Data"))
        etree.SubElement(x509_data, _ds("X509Certificate")).text = base64.b64encode(cert_der).decode()
        rsa_kv = etree.SubElement(etree.SubElement(key_info, _ds("KeyValue")), _ds("RSAKeyValue"))
        etree.SubElement(rsa_kv, _ds("Modulus")).text = _b64_entero(numeros.n)
        etree.SubElement(rsa_kv, _ds("Exponent")).text = _b64_entero(numeros.e)

        obj = etree.SubElement(firma, _ds("Object"))
        qp = etree.SubElement(obj, _etsi("QualifyingProperties"), nsmap={"etsi": ETSI}, Target=f"#{sig_id}")
        sp = etree.SubElement(qp, _etsi("SignedProperties"), Id=props_id)
        ssp = etree.SubElement(sp, _etsi("SignedSignatureProperties"))
        etree.SubElement(ssp, _etsi("SigningTime")).text = momento.isoformat(timespec="seconds")
        cert_el = etree.SubElement(etree.SubElement(ssp, _etsi("SigningCertificate")), _etsi("Cert"))
        cert_digest = etree.SubElement(cert_el, _etsi("CertDigest"))
        etree.SubElement(cert_digest, _ds("DigestMethod"), Algorithm=SHA1)
        etree.SubElement(cert_digest, _ds("DigestValue")).text = _sha1_b64(cert_der)
        issuer = etree.SubElement(cert_el, _etsi("IssuerSerial"))
        etree.SubElement(issuer, _ds("X509IssuerName")).text = self.certificado.issuer.rfc4514_string()
        etree.SubElement(issuer, _ds("X509SerialNumber")).text = str(self.certificado.serial_number)
        sdop = etree.SubElement(sp, _etsi("SignedDataObjectProperties"))
        dof = etree.SubElement(sdop, _etsi("DataObjectFormat"), ObjectReference=f"#{ref_id}")
        etree.SubElement(dof, _etsi("Description")).text = "contenido comprobante"
        etree.SubElement(dof, _etsi("MimeType")).text = "text/xml"

        # 2) Digests de las propiedades firmadas y del certificado, ya dentro del documento
        #    (la C14N inclusiva hereda los namespaces de sus padres).
        ref_props.find(_ds("DigestValue")).text = _sha1_b64(_c14n(sp))
        ref_cert.find(_ds("DigestValue")).text = _sha1_b64(_c14n(key_info))

        # 3) Firma RSA-SHA1 del SignedInfo canonicalizado.
        firma_bytes = self.clave_privada.sign(_c14n(signed_info), padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
        valor_firma.text = base64.b64encode(firma_bytes).decode()

        return etree.tostring(doc, xml_declaration=True, encoding="UTF-8")


def verificar_firma(xml_firmado: bytes) -> bool:
    """Comprueba digests y firma RSA de un XML firmado con :meth:`Firmador.firmar`.

    Sirve para detectar en tu sistema un XML alterado antes de enviarlo.
    No valida la cadena de confianza del certificado (eso lo hace el SRI).
    """
    doc = etree.fromstring(xml_firmado)
    firma = doc.find(_ds("Signature"))
    if firma is None:
        return False
    signed_info = firma.find(_ds("SignedInfo"))
    por_id = {el.get("Id"): el for el in firma.iter() if el.get("Id")}
    for ref in signed_info.findall(_ds("Reference")):
        uri = ref.get("URI", "")
        esperado = ref.findtext(_ds("DigestValue"))
        if uri == "#comprobante":
            copia = etree.fromstring(etree.tostring(doc))
            copia.remove(copia.find(_ds("Signature")))
            datos = _c14n(copia)
        else:
            objetivo = por_id.get(uri.lstrip("#"))
            if objetivo is None:
                return False
            datos = _c14n(objetivo)
        if _sha1_b64(datos) != esperado:
            return False
    cert_b64 = firma.findtext(f"{_ds('KeyInfo')}/{_ds('X509Data')}/{_ds('X509Certificate')}")
    cert = x509.load_der_x509_certificate(base64.b64decode(cert_b64))
    try:
        cert.public_key().verify(
            base64.b64decode(firma.findtext(_ds("SignatureValue"))),
            _c14n(signed_info),
            padding.PKCS1v15(),
            hashes.SHA1(),  # noqa: S303
        )
    except Exception:  # noqa: BLE001
        return False
    return True
