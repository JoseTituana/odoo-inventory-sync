"""RIDE: la representación impresa (PDF) del comprobante autorizado.

Necesita ``reportlab`` (``pip install sri-core[ride]``).
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from .catalogos import Ambiente, IVA_CODIGOS
from .modelos import Factura, NotaCredito, _Comprobante
from .sri_cliente import RespuestaAutorizacion

NOMBRES_PAGO = {
    "01": "Sin utilización del sistema financiero",
    "15": "Compensación de deudas",
    "16": "Tarjeta de débito",
    "17": "Dinero electrónico",
    "18": "Tarjeta prepago",
    "19": "Tarjeta de crédito",
    "20": "Otros con utilización del sistema financiero",
    "21": "Endoso de títulos",
}


def _precio(valor: Decimal) -> str:
    """Entre 2 y 4 decimales: 2.50, 39.1304."""
    texto = f"{valor:.4f}".rstrip("0")
    entero, _, dec = texto.partition(".")
    return f"{entero}.{dec.ljust(2, '0')}"


def _p(texto, estilo):
    from reportlab.platypus import Paragraph

    return Paragraph(escape(str(texto)), estilo)


def generar_ride(
    comprobante: _Comprobante,
    autorizacion: RespuestaAutorizacion | None = None,
    logo: bytes | str | None = None,
) -> bytes:
    """Devuelve el PDF (bytes). Sin ``autorizacion`` sale marcado como pendiente."""
    from reportlab.graphics.barcode.code128 import Code128
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer, Table, TableStyle

    e, comp = comprobante.emisor, comprobante.comprador
    lineas, totales = comprobante.calcular()
    es_factura = isinstance(comprobante, Factura)
    titulo = "FACTURA" if es_factura else "NOTA DE CRÉDITO"

    normal = ParagraphStyle("n", fontName="Helvetica", fontSize=8, leading=10)
    negrita = ParagraphStyle("b", parent=normal, fontName="Helvetica-Bold")
    grande = ParagraphStyle("g", parent=negrita, fontSize=12, leading=15)
    gris = colors.HexColor("#999999")

    ancho = A4[0] - 24 * mm
    mitad = ancho / 2 - 2 * mm

    # --- Encabezado izquierdo: datos del emisor ---------------------------
    izq = []
    if logo:
        img = Image(BytesIO(logo) if isinstance(logo, bytes) else logo)
        escala = min(60 * mm / img.imageWidth, 25 * mm / img.imageHeight)
        img.drawWidth, img.drawHeight = img.imageWidth * escala, img.imageHeight * escala
        izq += [img, Spacer(1, 3 * mm)]
    izq.append(_p(e.razon_social, grande))
    if e.nombre_comercial:
        izq.append(_p(e.nombre_comercial, negrita))
    izq.append(_p(f"Dirección matriz: {e.dir_matriz}", normal))
    if e.dir_establecimiento:
        izq.append(_p(f"Dirección sucursal: {e.dir_establecimiento}", normal))
    if e.contribuyente_especial:
        izq.append(_p(f"Contribuyente especial Nro.: {e.contribuyente_especial}", normal))
    izq.append(_p(f"Obligado a llevar contabilidad: {'SI' if e.obligado_contabilidad else 'NO'}", normal))
    if e.contribuyente_rimpe:
        izq.append(_p(e.contribuyente_rimpe, normal))
    if e.agente_retencion:
        izq.append(_p(f"Agente de retención Resolución No. {e.agente_retencion}", normal))

    # --- Encabezado derecho: RUC, número, autorización, clave -------------
    autorizado = autorizacion is not None and autorizacion.autorizado
    num_aut = autorizacion.numero_autorizacion if autorizado else "PENDIENTE DE AUTORIZACIÓN"
    fecha_aut = (
        autorizacion.fecha_autorizacion.strftime("%d/%m/%Y %H:%M:%S")
        if autorizado and autorizacion.fecha_autorizacion
        else "-"
    )
    der = [
        _p(f"R.U.C.: {e.ruc}", grande),
        _p(titulo, grande),
        _p(f"No. {comprobante.numero}", negrita),
        Spacer(1, 2 * mm),
        _p("NÚMERO DE AUTORIZACIÓN", negrita),
        _p(num_aut, normal),
        _p(f"FECHA Y HORA DE AUTORIZACIÓN: {fecha_aut}", normal),
        _p(f"AMBIENTE: {'PRODUCCIÓN' if e.ambiente is Ambiente.PRODUCCION else 'PRUEBAS'}", normal),
        _p("EMISIÓN: NORMAL", normal),
        Spacer(1, 2 * mm),
        _p("CLAVE DE ACCESO", negrita),
        Code128(comprobante.clave_acceso, barHeight=12 * mm, barWidth=0.21 * mm, quiet=False),
        _p(comprobante.clave_acceso, ParagraphStyle("c", parent=normal, fontSize=7)),
    ]

    caja = TableStyle([("BOX", (0, 0), (-1, -1), 0.6, gris), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    encabezado = Table([[izq, der]], colWidths=[mitad, mitad + 4 * mm])
    encabezado.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (1, 0), (1, 0), 0.6, gris)]))

    # --- Datos del comprador ---------------------------------------------
    filas_cliente = [
        [_p("Razón social / Nombres:", negrita), _p(comp.razon_social, normal),
         _p("Identificación:", negrita), _p(comp.identificacion, normal)],
        [_p("Fecha de emisión:", negrita), _p(comprobante.fecha.strftime("%d/%m/%Y"), normal),
         _p("Dirección:", negrita), _p(comp.direccion or "-", normal)],
    ]
    if isinstance(comprobante, NotaCredito):
        filas_cliente += [
            [_p("Comprobante que se modifica:", negrita), _p(f"FACTURA {comprobante.factura_numero}", normal),
             _p("Fecha de emisión (comprobante a modificar):", negrita),
             _p(comprobante.factura_fecha.strftime("%d/%m/%Y"), normal)],
            [_p("Razón de modificación:", negrita), _p(comprobante.motivo, normal), "", ""],
        ]
    cliente = Table(filas_cliente, colWidths=[ancho * 0.2, ancho * 0.35, ancho * 0.2, ancho * 0.25])
    cliente.setStyle(caja)

    # --- Detalle -----------------------------------------------------------
    cab = ["Cód. principal", "Cant.", "Descripción", "P. unitario", "Descuento", "Precio total"]
    filas = [[_p(c, negrita) for c in cab]]
    for ln in lineas:
        filas.append([
            _p(ln.item.codigo, normal),
            f"{ln.item.cantidad.normalize():f}",
            _p(ln.item.descripcion, normal),
            _precio(ln.precio_unitario),
            f"{ln.descuento:.2f}",
            f"{ln.base:.2f}",
        ])
    detalle = Table(filas, colWidths=[ancho * 0.15, ancho * 0.08, ancho * 0.41, ancho * 0.12, ancho * 0.11, ancho * 0.13], repeatRows=1)
    detalle.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, gris),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    # --- Totales -----------------------------------------------------------
    nombre_tarifa = {v: k for k, v in IVA_CODIGOS.items()}
    filas_tot = []
    for imp in sorted(totales.impuestos, key=lambda i: -i.tarifa):
        etiqueta = nombre_tarifa[imp.codigo_porcentaje]
        etiqueta = {"no_objeto": "NO OBJETO DE IVA", "exento": "EXENTO DE IVA"}.get(etiqueta, f"{etiqueta}%")
        filas_tot.append([f"SUBTOTAL {etiqueta}", f"{imp.base:.2f}"])
    filas_tot += [
        ["SUBTOTAL SIN IMPUESTOS", f"{totales.subtotal_sin_impuestos:.2f}"],
        ["TOTAL DESCUENTO", f"{totales.total_descuento:.2f}"],
    ]
    for imp in totales.impuestos:
        if imp.tarifa > 0:
            filas_tot.append([f"IVA {imp.tarifa.normalize():f}%", f"{imp.valor:.2f}"])
    if es_factura:
        filas_tot.append(["PROPINA", f"{totales.propina:.2f}"])
    filas_tot.append(["VALOR TOTAL", f"{totales.importe_total:.2f}"])
    tabla_tot = Table(filas_tot, colWidths=[ancho * 0.25, ancho * 0.13])
    tabla_tot.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, gris),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))

    # --- Información adicional y formas de pago ----------------------------
    adicional = [_p("Información adicional", negrita)]
    extras = {"Dirección": comp.direccion, "Email": comp.email, "Teléfono": comp.telefono, **comprobante.info_adicional}
    for k, v in extras.items():
        if v:
            adicional.append(_p(f"{k}: {v}", normal))
    if es_factura:
        adicional += [Spacer(1, 3 * mm), _p("Forma de pago", negrita)]
        for pago in comprobante.pagos:
            adicional.append(_p(f"{NOMBRES_PAGO.get(pago.codigo, pago.codigo)}: {pago.total:.2f}", normal))
    pie = Table([[adicional, tabla_tot]], colWidths=[ancho * 0.6, ancho * 0.4])
    pie.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (0, 0), 0.6, gris)]))

    salida = BytesIO()
    doc = SimpleDocTemplate(
        salida, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"{titulo} {comprobante.numero}", author=e.razon_social,
    )
    doc.build([encabezado, Spacer(1, 4 * mm), cliente, Spacer(1, 4 * mm), detalle, Spacer(1, 4 * mm), pie])
    return salida.getvalue()
