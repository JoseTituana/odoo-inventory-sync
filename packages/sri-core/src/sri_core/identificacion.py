"""Validación de cédula y RUC ecuatorianos.

La cédula se valida con su dígito verificador (módulo 10). Para el RUC solo se
revisa el formato: el SRI ha emitido RUC de sociedades cuyo dígito verificador
no cumple el módulo 11, así que rechazarlos daría falsos errores.
"""
from __future__ import annotations


def validar_cedula(cedula: str) -> bool:
    if len(cedula) != 10 or not cedula.isdigit():
        return False
    provincia = int(cedula[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False
    if int(cedula[2]) >= 6:
        return False
    total = 0
    for i, d in enumerate(cedula[:9]):
        n = int(d) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    verificador = (10 - total % 10) % 10
    return verificador == int(cedula[9])


def validar_ruc(ruc: str) -> bool:
    if len(ruc) != 13 or not ruc.isdigit() or ruc.endswith("000"):
        return False
    provincia = int(ruc[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False
    tercero = int(ruc[2])
    if tercero < 6:  # persona natural: cédula + establecimiento
        return validar_cedula(ruc[:10])
    return tercero in (6, 9)
