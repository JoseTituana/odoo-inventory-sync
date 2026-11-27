"""Clave de acceso de 49 dígitos (ficha técnica, sección "Clave de acceso").

Estructura:
    fecha ddmmaaaa (8) + tipo comprobante (2) + RUC (13) + ambiente (1)
    + serie estab+ptoEmi (6) + secuencial (9) + código numérico (8)
    + tipo de emisión (1) + dígito verificador módulo 11 (1)
"""
from __future__ import annotations

import secrets
from datetime import date

from .catalogos import Ambiente, TipoComprobante

TIPO_EMISION_NORMAL = "1"


def digito_modulo11(cadena: str) -> str:
    """Dígito verificador módulo 11 con pesos 2..7 desde la derecha.

    Si el resultado es 11 se usa 0; si es 10 se usa 1 (regla del SRI).
    """
    if not cadena.isdigit():
        raise ValueError("La cadena debe tener solo dígitos")
    total = 0
    peso = 2
    for digito in reversed(cadena):
        total += int(digito) * peso
        peso = 2 if peso == 7 else peso + 1
    resultado = 11 - (total % 11)
    if resultado == 11:
        return "0"
    if resultado == 10:
        return "1"
    return str(resultado)


def generar_clave_acceso(
    fecha: date,
    tipo: TipoComprobante | str,
    ruc: str,
    ambiente: Ambiente | str,
    estab: str,
    pto_emi: str,
    secuencial: int | str,
    codigo_numerico: str | None = None,
) -> str:
    """Arma la clave de acceso y su dígito verificador.

    ``codigo_numerico`` son 8 dígitos libres; si no se da se genera al azar.
    Guárdalo junto a la factura: reenviar con otro código crea otra clave.
    """
    tipo = TipoComprobante(tipo).value
    ambiente = Ambiente(ambiente).value
    if codigo_numerico is None:
        codigo_numerico = f"{secrets.randbelow(10**8):08d}"
    partes = {
        "ruc": (ruc, 13),
        "estab": (estab, 3),
        "pto_emi": (pto_emi, 3),
        "secuencial": (str(secuencial).zfill(9), 9),
        "codigo_numerico": (codigo_numerico, 8),
    }
    for nombre, (valor, largo) in partes.items():
        if not (valor.isdigit() and len(valor) == largo):
            raise ValueError(f"{nombre} debe tener {largo} dígitos, se recibió {valor!r}")
    base = (
        fecha.strftime("%d%m%Y")
        + tipo
        + ruc
        + ambiente
        + estab
        + pto_emi
        + str(secuencial).zfill(9)
        + codigo_numerico
        + TIPO_EMISION_NORMAL
    )
    return base + digito_modulo11(base)


def validar_clave_acceso(clave: str) -> bool:
    return len(clave) == 49 and clave.isdigit() and digito_modulo11(clave[:48]) == clave[48]
