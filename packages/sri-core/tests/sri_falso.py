"""Respuestas SOAP del SRI simuladas, para probar sin internet."""


def sobre(cuerpo):
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f"{cuerpo}</soap:Body></soap:Envelope>"
    ).encode()


def recibida():
    return sobre('<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">'
                 "<RespuestaRecepcionComprobante><estado>RECIBIDA</estado><comprobantes/>"
                 "</RespuestaRecepcionComprobante></ns2:validarComprobanteResponse>")


def devuelta(ident, texto, extra=""):
    return sobre('<ns2:validarComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.recepcion">'
                 "<RespuestaRecepcionComprobante><estado>DEVUELTA</estado><comprobantes><comprobante>"
                 "<claveAcceso>x</claveAcceso><mensajes><mensaje>"
                 f"<identificador>{ident}</identificador><mensaje>{texto}</mensaje>"
                 f"<informacionAdicional>{extra}</informacionAdicional><tipo>ERROR</tipo>"
                 "</mensaje></mensajes></comprobante></comprobantes></RespuestaRecepcionComprobante>"
                 "</ns2:validarComprobanteResponse>")


def autorizacion(clave, estado="AUTORIZADO", mensajes=""):
    return sobre('<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
                 f"<RespuestaAutorizacionComprobante><claveAccesoConsultada>{clave}</claveAccesoConsultada>"
                 "<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>"
                 f"<estado>{estado}</estado><numeroAutorizacion>{clave}</numeroAutorizacion>"
                 "<fechaAutorizacion>2026-11-27T11:30:05-05:00</fechaAutorizacion><ambiente>PRUEBAS</ambiente>"
                 "<comprobante><![CDATA[<factura id=\"comprobante\"/>]]></comprobante>"
                 f"<mensajes>{mensajes}</mensajes></autorizacion></autorizaciones>"
                 "</RespuestaAutorizacionComprobante></ns2:autorizacionComprobanteResponse>")


def sin_autorizaciones(clave):
    return sobre('<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
                 f"<RespuestaAutorizacionComprobante><claveAccesoConsultada>{clave}</claveAccesoConsultada>"
                 "<numeroComprobantes>0</numeroComprobantes><autorizaciones/>"
                 "</RespuestaAutorizacionComprobante></ns2:autorizacionComprobanteResponse>")


class SesionFalsa:
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def post(self, url, data, headers, timeout):
        self.llamadas.append((url, data))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r

        class R:
            status_code = 200
            content = r
        return R()


class SRIQueAutoriza(SesionFalsa):
    """Recibe y autoriza todo lo que se le envía (lee la clave del XML enviado)."""

    def __init__(self, caido=False):
        super().__init__([])
        self.caido = caido

    def post(self, url, data, headers, timeout):
        import base64, re
        import requests
        self.llamadas.append((url, data))
        if self.caido:
            raise requests.ConnectionError("sin internet")
        texto = data.decode()
        if "validarComprobante" in texto:
            xml = base64.b64decode(re.search(r"<xml>(.*)</xml>", texto).group(1)).decode()
            self.ultima_clave = re.search(r"<claveAcceso>(\d{49})</claveAcceso>", xml).group(1)
            contenido = recibida()
        else:
            clave = re.search(r"<claveAccesoComprobante>(\d+)<", texto).group(1)
            contenido = autorizacion(clave)

        class R:
            status_code = 200
        R.content = contenido
        return R()
