"""Códigos oficiales de la ficha técnica del SRI (esquema offline).

Se agrupan aquí para que ningún otro archivo tenga "números mágicos".
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum


class Ambiente(str, Enum):
    """Ambiente del SRI. Pruebas no tiene valor legal."""

    PRUEBAS = "1"
    PRODUCCION = "2"


class TipoComprobante(str, Enum):
    FACTURA = "01"
    LIQUIDACION_COMPRA = "03"
    NOTA_CREDITO = "04"
    NOTA_DEBITO = "05"
    GUIA_REMISION = "06"
    RETENCION = "07"


class TipoIdentificacion(str, Enum):
    RUC = "04"
    CEDULA = "05"
    PASAPORTE = "06"
    CONSUMIDOR_FINAL = "07"
    EXTERIOR = "08"


# Código de impuesto (tabla 16): 2 = IVA, 3 = ICE, 5 = IRBPNR
CODIGO_IVA = "2"

# Tarifa de IVA (en %) -> codigoPorcentaje (tabla 17).
# "no_objeto" y "exento" no tienen tarifa numérica.
IVA_CODIGOS: dict[str, str] = {
    "0": "0",
    "12": "2",
    "14": "3",
    "15": "4",
    "5": "5",
    "no_objeto": "6",
    "exento": "7",
    "13": "10",
}


def iva_codigo_y_tarifa(iva: str | int | Decimal) -> tuple[str, Decimal]:
    """Devuelve (codigoPorcentaje, tarifa en %) para una tarifa de IVA.

    >>> iva_codigo_y_tarifa(15)
    ('4', Decimal('15'))
    """
    clave = str(iva).strip().lower()
    if clave in ("no_objeto", "exento"):
        return IVA_CODIGOS[clave], Decimal("0")
    try:
        clave = str(Decimal(clave).normalize())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Tarifa de IVA desconocida: {iva!r}") from exc
    if clave not in IVA_CODIGOS:
        raise ValueError(
            f"Tarifa de IVA {iva!r} no está en la tabla del SRI. "
            f"Valores válidos: {', '.join(IVA_CODIGOS)}"
        )
    return IVA_CODIGOS[clave], Decimal(clave)


# Formas de pago (tabla 24). Se aceptan alias en español sencillo.
FORMAS_PAGO: dict[str, str] = {
    "efectivo": "01",
    "sin_sistema_financiero": "01",
    "compensacion": "15",
    "tarjeta_debito": "16",
    "dinero_electronico": "17",
    "tarjeta_prepago": "18",
    "tarjeta_credito": "19",
    "transferencia": "20",
    "otros_sistema_financiero": "20",
    "endoso_titulos": "21",
}


def forma_pago_codigo(forma: str) -> str:
    forma = str(forma).strip().lower()
    if forma in FORMAS_PAGO.values():
        return forma
    if forma not in FORMAS_PAGO:
        raise ValueError(
            f"Forma de pago desconocida: {forma!r}. Usa una de: {', '.join(FORMAS_PAGO)}"
        )
    return FORMAS_PAGO[forma]


CONSUMIDOR_FINAL_ID = "9999999999999"
CONSUMIDOR_FINAL_NOMBRE = "CONSUMIDOR FINAL"
# Una factura a consumidor final no puede superar este importe total (USD).
CONSUMIDOR_FINAL_LIMITE = Decimal("50.00")

URLS = {
    Ambiente.PRUEBAS: "https://celcer.sri.gob.ec/comprobantes-electronicos-ws/",
    Ambiente.PRODUCCION: "https://cel.sri.gob.ec/comprobantes-electronicos-ws/",
}
