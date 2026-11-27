"""Arma el XML de factura (v1.1.0) y nota de crédito (v1.1.0) del SRI.

El orden de las etiquetas importa: el SRI valida contra su XSD y rechaza
un XML con etiquetas fuera de orden (error 35, "ARCHIVO NO CUMPLE ESTRUCTURA XML").
"""
from __future__ import annotations

from decimal import Decimal

from lxml import etree

from .catalogos import CODIGO_IVA, TipoComprobante
from .modelos import Factura, LineaCalculada, NotaCredito, Totales, _Comprobante

MAX_CAMPOS_ADICIONALES = 15


def _m(valor: Decimal) -> str:
    """Dinero con 2 decimales."""
    return f"{valor:.2f}"


def _m6(valor: Decimal) -> str:
    """Cantidad o precio unitario con hasta 6 decimales (versión 1.1.0)."""
    return f"{valor:.6f}"


def _limpio(texto: str, largo: int = 300) -> str:
    return " ".join(str(texto).split())[:largo]


def _sub(padre: etree._Element, nombre: str, texto: str | None = None) -> etree._Element:
    el = etree.SubElement(padre, nombre)
    if texto is not None:
        el.text = texto
    return el


def _info_tributaria(raiz: etree._Element, c: _Comprobante, tipo: TipoComprobante) -> None:
    e = c.emisor
    it = _sub(raiz, "infoTributaria")
    _sub(it, "ambiente", e.ambiente.value)
    _sub(it, "tipoEmision", "1")
    _sub(it, "razonSocial", _limpio(e.razon_social))
    if e.nombre_comercial:
        _sub(it, "nombreComercial", _limpio(e.nombre_comercial))
    _sub(it, "ruc", e.ruc)
    _sub(it, "claveAcceso", c.clave_acceso)
    _sub(it, "codDoc", tipo.value)
    _sub(it, "estab", e.estab)
    _sub(it, "ptoEmi", e.pto_emi)
    _sub(it, "secuencial", f"{int(c.secuencial):09d}")
    _sub(it, "dirMatriz", _limpio(e.dir_matriz))
    if e.agente_retencion:
        _sub(it, "agenteRetencion", e.agente_retencion)
    if e.contribuyente_rimpe:
        _sub(it, "contribuyenteRimpe", _limpio(e.contribuyente_rimpe))


def _total_con_impuestos(padre: etree._Element, totales: Totales, con_tarifa: bool) -> None:
    tci = _sub(padre, "totalConImpuestos")
    for imp in totales.impuestos:
        ti = _sub(tci, "totalImpuesto")
        _sub(ti, "codigo", CODIGO_IVA)
        _sub(ti, "codigoPorcentaje", imp.codigo_porcentaje)
        _sub(ti, "baseImponible", _m(imp.base))
        if con_tarifa:
            _sub(ti, "tarifa", f"{imp.tarifa:.2f}")
        _sub(ti, "valor", _m(imp.valor))


def _impuestos_detalle(detalle: etree._Element, ln: LineaCalculada) -> None:
    imps = _sub(detalle, "impuestos")
    imp = _sub(imps, "impuesto")
    _sub(imp, "codigo", CODIGO_IVA)
    _sub(imp, "codigoPorcentaje", ln.codigo_porcentaje)
    _sub(imp, "tarifa", f"{ln.tarifa:.2f}")
    _sub(imp, "baseImponible", _m(ln.base))
    _sub(imp, "valor", _m(ln.iva))


def _info_adicional(raiz: etree._Element, c: _Comprobante) -> None:
    campos: dict[str, str] = {}
    comp = c.comprador
    if comp.direccion:
        campos["Dirección"] = comp.direccion
    if comp.email:
        campos["Email"] = comp.email
    if comp.telefono:
        campos["Teléfono"] = comp.telefono
    campos.update({k: v for k, v in c.info_adicional.items() if v})
    if not campos:
        return
    if len(campos) > MAX_CAMPOS_ADICIONALES:
        raise ValueError(f"El SRI acepta hasta {MAX_CAMPOS_ADICIONALES} campos adicionales")
    ia = _sub(raiz, "infoAdicional")
    for nombre, valor in campos.items():
        campo = _sub(ia, "campoAdicional", _limpio(valor))
        campo.set("nombre", _limpio(nombre))


def _bytes(raiz: etree._Element) -> bytes:
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def factura_xml(f: Factura) -> bytes:
    lineas, totales = f.calcular()
    e, comp = f.emisor, f.comprador
    raiz = etree.Element("factura", id="comprobante", version="1.1.0")
    _info_tributaria(raiz, f, TipoComprobante.FACTURA)

    inf = _sub(raiz, "infoFactura")
    _sub(inf, "fechaEmision", f.fecha.strftime("%d/%m/%Y"))
    if e.dir_establecimiento:
        _sub(inf, "dirEstablecimiento", _limpio(e.dir_establecimiento))
    if e.contribuyente_especial:
        _sub(inf, "contribuyenteEspecial", e.contribuyente_especial)
    _sub(inf, "obligadoContabilidad", "SI" if e.obligado_contabilidad else "NO")
    _sub(inf, "tipoIdentificacionComprador", comp.tipo.value)
    _sub(inf, "razonSocialComprador", _limpio(comp.razon_social))
    _sub(inf, "identificacionComprador", comp.identificacion)
    if comp.direccion:
        _sub(inf, "direccionComprador", _limpio(comp.direccion))
    _sub(inf, "totalSinImpuestos", _m(totales.subtotal_sin_impuestos))
    _sub(inf, "totalDescuento", _m(totales.total_descuento))
    _total_con_impuestos(inf, totales, con_tarifa=False)
    _sub(inf, "propina", _m(totales.propina))
    _sub(inf, "importeTotal", _m(totales.importe_total))
    _sub(inf, "moneda", "DOLAR")
    pagos = _sub(inf, "pagos")
    for p in f.pagos:
        pago = _sub(pagos, "pago")
        _sub(pago, "formaPago", p.codigo)
        _sub(pago, "total", _m(p.total))
        if p.plazo is not None:
            _sub(pago, "plazo", str(p.plazo))
            _sub(pago, "unidadTiempo", p.unidad_tiempo or "dias")

    detalles = _sub(raiz, "detalles")
    for ln in lineas:
        det = _sub(detalles, "detalle")
        _sub(det, "codigoPrincipal", _limpio(ln.item.codigo, 25))
        if ln.item.codigo_auxiliar:
            _sub(det, "codigoAuxiliar", _limpio(ln.item.codigo_auxiliar, 25))
        _sub(det, "descripcion", _limpio(ln.item.descripcion))
        _sub(det, "cantidad", _m6(ln.item.cantidad))
        _sub(det, "precioUnitario", _m6(ln.precio_unitario))
        _sub(det, "descuento", _m(ln.descuento))
        _sub(det, "precioTotalSinImpuesto", _m(ln.base))
        _impuestos_detalle(det, ln)

    _info_adicional(raiz, f)
    return _bytes(raiz)


def nota_credito_xml(nc: NotaCredito) -> bytes:
    lineas, totales = nc.calcular()
    e, comp = nc.emisor, nc.comprador
    raiz = etree.Element("notaCredito", id="comprobante", version="1.1.0")
    _info_tributaria(raiz, nc, TipoComprobante.NOTA_CREDITO)

    inf = _sub(raiz, "infoNotaCredito")
    _sub(inf, "fechaEmision", nc.fecha.strftime("%d/%m/%Y"))
    if e.dir_establecimiento:
        _sub(inf, "dirEstablecimiento", _limpio(e.dir_establecimiento))
    _sub(inf, "tipoIdentificacionComprador", comp.tipo.value)
    _sub(inf, "razonSocialComprador", _limpio(comp.razon_social))
    _sub(inf, "identificacionComprador", comp.identificacion)
    if e.contribuyente_especial:
        _sub(inf, "contribuyenteEspecial", e.contribuyente_especial)
    _sub(inf, "obligadoContabilidad", "SI" if e.obligado_contabilidad else "NO")
    _sub(inf, "codDocModificado", TipoComprobante.FACTURA.value)
    _sub(inf, "numDocModificado", nc.factura_numero)
    _sub(inf, "fechaEmisionDocSustento", nc.factura_fecha.strftime("%d/%m/%Y"))
    _sub(inf, "totalSinImpuestos", _m(totales.subtotal_sin_impuestos))
    _sub(inf, "valorModificacion", _m(totales.importe_total))
    _sub(inf, "moneda", "DOLAR")
    _total_con_impuestos(inf, totales, con_tarifa=False)
    _sub(inf, "motivo", _limpio(nc.motivo))

    detalles = _sub(raiz, "detalles")
    for ln in lineas:
        det = _sub(detalles, "detalle")
        _sub(det, "codigoInterno", _limpio(ln.item.codigo, 25))
        if ln.item.codigo_auxiliar:
            _sub(det, "codigoAdicional", _limpio(ln.item.codigo_auxiliar, 25))
        _sub(det, "descripcion", _limpio(ln.item.descripcion))
        _sub(det, "cantidad", _m6(ln.item.cantidad))
        _sub(det, "precioUnitario", _m6(ln.precio_unitario))
        _sub(det, "descuento", _m(ln.descuento))
        _sub(det, "precioTotalSinImpuesto", _m(ln.base))
        _impuestos_detalle(det, ln)

    _info_adicional(raiz, nc)
    return _bytes(raiz)


def comprobante_xml(c: _Comprobante) -> bytes:
    if isinstance(c, Factura):
        return factura_xml(c)
    if isinstance(c, NotaCredito):
        return nota_credito_xml(c)
    raise TypeError(f"Tipo de comprobante no soportado: {type(c).__name__}")
