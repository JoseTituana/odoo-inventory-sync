"""Envío al SRI (recepción) y consulta de autorización por SOAP.

Se usa ``requests`` con el sobre SOAP escrito a mano en vez de una librería
SOAP: son solo dos operaciones y así no dependemos de descargar el WSDL.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests
from lxml import etree

from .catalogos import URLS, Ambiente

RECEPCION = "RecepcionComprobantesOffline"
AUTORIZACION = "AutorizacionComprobantesOffline"

# Mensajes del SRI que significan "todavía no, vuelve a consultar".
EN_PROCESAMIENTO = {"70"}
# "CLAVE ACCESO REGISTRADA": ya se había recibido; basta con consultar la autorización.
YA_RECIBIDA = {"43", "45"}


class SRIConexionError(Exception):
    """El SRI no respondió o respondió algo que no es SOAP. Se puede reintentar."""


@dataclass
class MensajeSRI:
    identificador: str
    mensaje: str
    informacion_adicional: str = ""
    tipo: str = ""

    def __str__(self) -> str:
        extra = f" ({self.informacion_adicional})" if self.informacion_adicional else ""
        return f"[{self.identificador}] {self.mensaje}{extra}"


@dataclass
class RespuestaRecepcion:
    estado: str  # RECIBIDA o DEVUELTA
    mensajes: list[MensajeSRI] = field(default_factory=list)

    @property
    def recibida(self) -> bool:
        return self.estado == "RECIBIDA" or any(m.identificador in YA_RECIBIDA for m in self.mensajes)


@dataclass
class RespuestaAutorizacion:
    estado: str  # AUTORIZADO, NO AUTORIZADO, EN PROCESO, o DEVUELTA si no pasó la recepción
    clave_acceso: str
    numero_autorizacion: str | None = None
    fecha_autorizacion: datetime | None = None
    ambiente: str | None = None
    comprobante_xml: bytes | None = None  # el XML que guarda el SRI
    mensajes: list[MensajeSRI] = field(default_factory=list)

    @property
    def autorizado(self) -> bool:
        return self.estado == "AUTORIZADO"

    def xml_autorizado(self) -> bytes:
        """XML de autorización completo, el que se entrega al cliente y se archiva."""
        if not self.autorizado:
            raise ValueError("El comprobante no está autorizado")
        raiz = etree.Element("autorizacion")
        etree.SubElement(raiz, "estado").text = self.estado
        etree.SubElement(raiz, "numeroAutorizacion").text = self.numero_autorizacion
        etree.SubElement(raiz, "fechaAutorizacion").text = (
            self.fecha_autorizacion.isoformat() if self.fecha_autorizacion else ""
        )
        etree.SubElement(raiz, "ambiente").text = self.ambiente or ""
        etree.SubElement(raiz, "comprobante").text = etree.CDATA((self.comprobante_xml or b"").decode("utf-8"))
        return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def _local(el: etree._Element, nombre: str) -> list[etree._Element]:
    return el.xpath(f".//*[local-name()='{nombre}']")


def _texto(el: etree._Element, nombre: str) -> str:
    hallados = _local(el, nombre)
    return (hallados[0].text or "").strip() if hallados else ""


def _mensajes(el: etree._Element) -> list[MensajeSRI]:
    return [
        MensajeSRI(
            identificador=_texto(m, "identificador"),
            mensaje=_texto(m, "mensaje"),
            informacion_adicional=_texto(m, "informacionAdicional"),
            tipo=_texto(m, "tipo"),
        )
        for m in _local(el, "mensaje")
        if len(m)  # el nodo <mensaje> contenedor, no el texto <mensaje> interno
    ]


def _parse_fecha(texto: str) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


class ClienteSRI:
    """Habla con los servicios web del SRI.

    ``sesion`` permite inyectar una ``requests.Session`` (pruebas, proxy).
    """

    def __init__(self, ambiente: Ambiente | str = Ambiente.PRUEBAS, timeout: float = 30, sesion=None):
        self.ambiente = Ambiente(ambiente)
        self.base = URLS[self.ambiente]
        self.timeout = timeout
        self.sesion = sesion or requests.Session()

    def _soap(self, servicio: str, ns: str, operacion: str, cuerpo: str) -> etree._Element:
        sobre = (
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:ec="{ns}"><soapenv:Header/><soapenv:Body>'
            f"<ec:{operacion}>{cuerpo}</ec:{operacion}></soapenv:Body></soapenv:Envelope>"
        )
        try:
            r = self.sesion.post(
                self.base + servicio,
                data=sobre.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SRIConexionError(f"No se pudo conectar con el SRI: {exc}") from exc
        if r.status_code >= 500 and b"Envelope" not in r.content:
            raise SRIConexionError(f"El SRI respondió HTTP {r.status_code}")
        try:
            return etree.fromstring(r.content)
        except etree.XMLSyntaxError as exc:
            raise SRIConexionError("El SRI respondió algo que no es XML") from exc

    def enviar(self, xml_firmado: bytes) -> RespuestaRecepcion:
        """Paso 1: recepción. El SRI revisa estructura y firma."""
        b64 = base64.b64encode(xml_firmado).decode()
        raiz = self._soap(RECEPCION, "http://ec.gob.sri.ws.recepcion", "validarComprobante", f"<xml>{b64}</xml>")
        estado = _texto(raiz, "estado")
        if not estado:
            raise SRIConexionError("Respuesta de recepción sin estado")
        return RespuestaRecepcion(estado=estado, mensajes=_mensajes(raiz))

    def autorizacion(self, clave_acceso: str) -> RespuestaAutorizacion:
        """Paso 2: consulta si el comprobante quedó autorizado."""
        raiz = self._soap(
            AUTORIZACION,
            "http://ec.gob.sri.ws.autorizacion",
            "autorizacionComprobante",
            f"<claveAccesoComprobante>{clave_acceso}</claveAccesoComprobante>",
        )
        autorizaciones = _local(raiz, "autorizacion")
        if not autorizaciones:
            return RespuestaAutorizacion("EN PROCESO", clave_acceso, mensajes=_mensajes(raiz))
        # A veces vienen varias (intentos rechazados y uno autorizado): gana la autorizada.
        elegida = next((a for a in autorizaciones if _texto(a, "estado") == "AUTORIZADO"), autorizaciones[0])
        comprobante = _texto(elegida, "comprobante")
        return RespuestaAutorizacion(
            estado=_texto(elegida, "estado"),
            clave_acceso=clave_acceso,
            numero_autorizacion=_texto(elegida, "numeroAutorizacion") or None,
            fecha_autorizacion=_parse_fecha(_texto(elegida, "fechaAutorizacion")),
            ambiente=_texto(elegida, "ambiente") or None,
            comprobante_xml=comprobante.encode("utf-8") if comprobante else None,
            mensajes=_mensajes(elegida),
        )

    def enviar_y_autorizar(
        self, xml_firmado: bytes, intentos: int = 5, espera: float = 3.0, dormir=time.sleep
    ) -> RespuestaAutorizacion:
        """Envía y consulta hasta obtener una respuesta final.

        Si al final sigue "EN PROCESO", guarda la clave de acceso y vuelve a
        llamar a :meth:`autorizacion` más tarde: el comprobante NO se reenvía
        con otra clave.
        """
        clave = _texto(etree.fromstring(xml_firmado), "claveAcceso")
        recepcion = self.enviar(xml_firmado)
        if not recepcion.recibida:
            return RespuestaAutorizacion("DEVUELTA", clave, mensajes=recepcion.mensajes)
        respuesta = RespuestaAutorizacion("EN PROCESO", clave)
        for intento in range(intentos):
            if intento:
                dormir(espera)
            respuesta = self.autorizacion(clave)
            en_proceso = respuesta.estado == "EN PROCESO" or any(
                m.identificador in EN_PROCESAMIENTO for m in respuesta.mensajes
            )
            if not en_proceso:
                return respuesta
        return respuesta
