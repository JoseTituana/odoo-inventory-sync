"""Datos de un comprobante y el cálculo de sus totales.

Todo el dinero se maneja con ``Decimal`` y redondeo "mitad hacia arriba",
que es como redondea el SRI. Nunca se usan ``float``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .catalogos import (
    CONSUMIDOR_FINAL_ID,
    CONSUMIDOR_FINAL_LIMITE,
    CONSUMIDOR_FINAL_NOMBRE,
    Ambiente,
    TipoComprobante,
    TipoIdentificacion,
    forma_pago_codigo,
    iva_codigo_y_tarifa,
)
from .clave_acceso import generar_clave_acceso
from .identificacion import validar_cedula, validar_ruc

CENTAVO = Decimal("0.01")
SEIS_DECIMALES = Decimal("0.000001")


def d(valor) -> Decimal:
    """Convierte a Decimal sin pasar por float (``d(0.1)`` -> ``Decimal('0.1')``)."""
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def q2(valor) -> Decimal:
    return d(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def q6(valor) -> Decimal:
    return d(valor).quantize(SEIS_DECIMALES, rounding=ROUND_HALF_UP)


@dataclass
class Emisor:
    """El negocio que factura. Un Emisor por cada RUC + establecimiento + punto de emisión."""

    ruc: str
    razon_social: str
    dir_matriz: str
    estab: str = "001"
    pto_emi: str = "001"
    nombre_comercial: str | None = None
    dir_establecimiento: str | None = None
    obligado_contabilidad: bool = False
    contribuyente_especial: str | None = None  # número de resolución, si aplica
    contribuyente_rimpe: str | None = None  # p. ej. "CONTRIBUYENTE RÉGIMEN RIMPE"
    agente_retencion: str | None = None  # número de resolución, si aplica
    ambiente: Ambiente = Ambiente.PRUEBAS

    def __post_init__(self) -> None:
        self.ambiente = Ambiente(self.ambiente)
        if not validar_ruc(self.ruc):
            raise ValueError(f"RUC del emisor no válido: {self.ruc!r}")
        for nombre in ("estab", "pto_emi"):
            valor = getattr(self, nombre)
            if not (valor.isdigit() and len(valor) == 3):
                raise ValueError(f"{nombre} debe tener 3 dígitos, p. ej. '001'")


@dataclass
class Comprador:
    tipo: TipoIdentificacion
    identificacion: str
    razon_social: str
    direccion: str | None = None
    email: str | None = None
    telefono: str | None = None

    def __post_init__(self) -> None:
        self.tipo = TipoIdentificacion(self.tipo)
        if self.tipo is TipoIdentificacion.CEDULA and not validar_cedula(self.identificacion):
            raise ValueError(f"Cédula no válida: {self.identificacion!r}")
        if self.tipo is TipoIdentificacion.RUC and not validar_ruc(self.identificacion):
            raise ValueError(f"RUC del comprador no válido: {self.identificacion!r}")

    @classmethod
    def consumidor_final(cls) -> Comprador:
        return cls(TipoIdentificacion.CONSUMIDOR_FINAL, CONSUMIDOR_FINAL_ID, CONSUMIDOR_FINAL_NOMBRE)

    @classmethod
    def desde_identificacion(cls, identificacion: str, razon_social: str, **extra) -> Comprador:
        """Elige el tipo según el número: 13 dígitos RUC, 10 cédula, otro pasaporte."""
        identificacion = identificacion.strip()
        if identificacion == CONSUMIDOR_FINAL_ID:
            return cls.consumidor_final()
        if identificacion.isdigit() and len(identificacion) == 13:
            tipo = TipoIdentificacion.RUC
        elif identificacion.isdigit() and len(identificacion) == 10:
            tipo = TipoIdentificacion.CEDULA
        else:
            tipo = TipoIdentificacion.PASAPORTE
        return cls(tipo, identificacion, razon_social, **extra)

    @property
    def es_consumidor_final(self) -> bool:
        return self.tipo is TipoIdentificacion.CONSUMIDOR_FINAL


@dataclass
class Item:
    """Una línea vendida.

    ``precio`` es por unidad. Si ``precio_incluye_iva`` es True (precio de
    percha, como en Josheps), la base y el IVA se sacan del precio; si es
    False, el IVA se suma encima. ``descuento`` va en los mismos términos
    que ``precio`` y es el total de la línea, no por unidad.
    """

    codigo: str
    descripcion: str
    cantidad: Decimal | int | str
    precio: Decimal | int | str
    iva: str | int = 15
    descuento: Decimal | int | str = 0
    precio_incluye_iva: bool = False
    codigo_auxiliar: str | None = None

    def __post_init__(self) -> None:
        self.cantidad = d(self.cantidad)
        self.precio = d(self.precio)
        self.descuento = d(self.descuento)
        iva_codigo_y_tarifa(self.iva)  # falla temprano si la tarifa no existe
        if self.cantidad <= 0:
            raise ValueError(f"Cantidad debe ser mayor que cero en {self.codigo!r}")
        if self.precio < 0 or self.descuento < 0:
            raise ValueError(f"Precio y descuento no pueden ser negativos en {self.codigo!r}")
        if self.descuento > self.cantidad * self.precio:
            raise ValueError(f"El descuento supera el valor de la línea {self.codigo!r}")


@dataclass
class Pago:
    forma: str
    total: Decimal | int | str
    plazo: int | None = None
    unidad_tiempo: str | None = None  # "dias", "meses"

    def __post_init__(self) -> None:
        self.codigo = forma_pago_codigo(self.forma)
        self.total = q2(self.total)


@dataclass
class LineaCalculada:
    item: Item
    precio_unitario: Decimal  # sin IVA, 6 decimales
    descuento: Decimal  # sin IVA
    base: Decimal  # precioTotalSinImpuesto
    codigo_porcentaje: str
    tarifa: Decimal
    iva: Decimal


@dataclass
class ImpuestoTotal:
    codigo_porcentaje: str
    tarifa: Decimal
    base: Decimal
    valor: Decimal


@dataclass
class Totales:
    subtotal_sin_impuestos: Decimal
    total_descuento: Decimal
    impuestos: list[ImpuestoTotal]
    propina: Decimal
    importe_total: Decimal

    def base_por_tarifa(self, codigo_porcentaje: str) -> Decimal:
        return sum((i.base for i in self.impuestos if i.codigo_porcentaje == codigo_porcentaje), Decimal("0.00"))

    @property
    def total_iva(self) -> Decimal:
        return sum((i.valor for i in self.impuestos), Decimal("0.00"))


def calcular_linea(item: Item) -> LineaCalculada:
    codigo, tarifa = iva_codigo_y_tarifa(item.iva)
    factor = 1 + tarifa / 100
    if item.precio_incluye_iva:
        neto_con_iva = item.cantidad * item.precio - item.descuento
        base = q2(neto_con_iva / factor)
        iva = q2(neto_con_iva) - base  # así la línea suma exactamente el precio de percha
        descuento = q2(item.descuento / factor)
        precio_unitario = q6((base + descuento) / item.cantidad)
    else:
        precio_unitario = q6(item.precio)
        descuento = q2(item.descuento)
        base = q2(item.cantidad * precio_unitario - descuento)
        iva = q2(base * tarifa / 100)
    return LineaCalculada(item, precio_unitario, descuento, base, codigo, tarifa, iva)


def calcular_totales(items: list[Item], propina=0) -> tuple[list[LineaCalculada], Totales]:
    if not items:
        raise ValueError("El comprobante necesita al menos un ítem")
    lineas = [calcular_linea(i) for i in items]
    grupos: dict[str, ImpuestoTotal] = {}
    for ln in lineas:
        g = grupos.setdefault(
            ln.codigo_porcentaje, ImpuestoTotal(ln.codigo_porcentaje, ln.tarifa, Decimal("0.00"), Decimal("0.00"))
        )
        g.base += ln.base
        g.valor += ln.iva
    subtotal = sum((ln.base for ln in lineas), Decimal("0.00"))
    total_desc = sum((ln.descuento for ln in lineas), Decimal("0.00"))
    propina = q2(propina)
    total_iva = sum((g.valor for g in grupos.values()), Decimal("0.00"))
    totales = Totales(subtotal, total_desc, list(grupos.values()), propina, subtotal + total_iva + propina)
    return lineas, totales


@dataclass
class _Comprobante:
    emisor: Emisor
    secuencial: int
    comprador: Comprador
    items: list[Item]
    fecha: date = field(default_factory=date.today)
    info_adicional: dict[str, str] = field(default_factory=dict)
    codigo_numerico: str | None = None
    clave_acceso: str = field(init=False)

    TIPO: TipoComprobante = field(init=False, repr=False)

    def _armar_clave(self) -> None:
        if not (1 <= int(self.secuencial) <= 999_999_999):
            raise ValueError("El secuencial va de 1 a 999999999")
        self.clave_acceso = generar_clave_acceso(
            self.fecha,
            self.TIPO,
            self.emisor.ruc,
            self.emisor.ambiente,
            self.emisor.estab,
            self.emisor.pto_emi,
            self.secuencial,
            self.codigo_numerico,
        )
        self.codigo_numerico = self.clave_acceso[39:47]

    @property
    def numero(self) -> str:
        """Número visible, p. ej. ``001-001-000000123``."""
        return f"{self.emisor.estab}-{self.emisor.pto_emi}-{int(self.secuencial):09d}"

    def calcular(self) -> tuple[list[LineaCalculada], Totales]:
        return calcular_totales(self.items, getattr(self, "propina", 0))


@dataclass
class Factura(_Comprobante):
    pagos: list[Pago] = field(default_factory=list)
    propina: Decimal | int | str = 0

    def __post_init__(self) -> None:
        self.TIPO = TipoComprobante.FACTURA
        self._armar_clave()
        _, totales = self.calcular()
        if not self.pagos:
            self.pagos = [Pago("efectivo", totales.importe_total)]
        suma_pagos = sum((p.total for p in self.pagos), Decimal("0.00"))
        if suma_pagos != totales.importe_total:
            raise ValueError(
                f"Los pagos suman {suma_pagos} pero la factura es de {totales.importe_total}"
            )
        if self.comprador.es_consumidor_final and totales.importe_total > CONSUMIDOR_FINAL_LIMITE:
            raise ValueError(
                f"A consumidor final no se puede facturar más de {CONSUMIDOR_FINAL_LIMITE} USD; "
                "pide los datos del cliente."
            )


@dataclass
class NotaCredito(_Comprobante):
    """Anula o corrige (total o parcialmente) una factura ya autorizada."""

    factura_numero: str = ""  # "001-001-000000123"
    factura_fecha: date | None = None
    motivo: str = "Devolución"

    def __post_init__(self) -> None:
        self.TIPO = TipoComprobante.NOTA_CREDITO
        partes = self.factura_numero.split("-")
        if len(partes) != 3 or [len(p) for p in partes] != [3, 3, 9] or not all(p.isdigit() for p in partes):
            raise ValueError("factura_numero debe tener la forma 001-001-000000123")
        if self.factura_fecha is None:
            raise ValueError("Falta factura_fecha (fecha de emisión de la factura que se modifica)")
        if self.comprador.es_consumidor_final:
            raise ValueError("El SRI no permite notas de crédito a consumidor final")
        self._armar_clave()
