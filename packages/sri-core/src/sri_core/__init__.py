"""sri-core: facturación electrónica del SRI de Ecuador como pieza reutilizable.

Flujo completo::

    from sri_core import *

    emisor = Emisor(ruc="...", razon_social="...", dir_matriz="...")
    factura = Factura(emisor, secuencial=1, comprador=Comprador.consumidor_final(),
                      items=[Item("A1", "Producto", 1, "11.50", precio_incluye_iva=True)])
    xml = comprobante_xml(factura)
    firmado = Firmador.desde_p12("firma.p12", "clave").firmar(xml)
    respuesta = ClienteSRI(emisor.ambiente).enviar_y_autorizar(firmado)
    pdf = generar_ride(factura, respuesta)
"""
from .catalogos import Ambiente, TipoComprobante, TipoIdentificacion
from .clave_acceso import generar_clave_acceso, validar_clave_acceso
from .firma import FirmaError, Firmador, InfoCertificado, verificar_firma
from .identificacion import validar_cedula, validar_ruc
from .modelos import Comprador, Emisor, Factura, Item, NotaCredito, Pago, Totales, calcular_totales
from .ride import generar_ride
from .sri_cliente import ClienteSRI, MensajeSRI, RespuestaAutorizacion, RespuestaRecepcion, SRIConexionError
from .xml_comprobantes import comprobante_xml, factura_xml, nota_credito_xml

__version__ = "0.1.0"

__all__ = [
    "Ambiente", "TipoComprobante", "TipoIdentificacion",
    "generar_clave_acceso", "validar_clave_acceso",
    "FirmaError", "Firmador", "InfoCertificado", "verificar_firma",
    "validar_cedula", "validar_ruc",
    "Comprador", "Emisor", "Factura", "Item", "NotaCredito", "Pago", "Totales", "calcular_totales",
    "generar_ride",
    "ClienteSRI", "MensajeSRI", "RespuestaAutorizacion", "RespuestaRecepcion", "SRIConexionError",
    "comprobante_xml", "factura_xml", "nota_credito_xml",
]
