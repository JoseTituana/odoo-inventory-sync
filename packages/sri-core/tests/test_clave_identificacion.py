import datetime as dt
import random

import pytest

from sri_core import generar_clave_acceso, validar_cedula, validar_clave_acceso, validar_ruc
from sri_core.clave_acceso import digito_modulo11


def digito_oca(clave):
    """Algoritmo del módulo OCA l10n_ec_account_edi, como referencia independiente."""
    mult, suma = 1, 0
    for i in reversed(range(len(clave))):
        mult += 1
        if mult == 8:
            mult = 2
        suma += int(clave[i]) * mult
    d = 11 - suma % 11
    return {11: 0, 10: 1}.get(d, d)


def test_digito_igual_que_oca():
    rnd = random.Random(7)
    for _ in range(2000):
        base = "".join(rnd.choice("0123456789") for _ in range(48))
        assert digito_modulo11(base) == str(digito_oca(base))


def test_estructura_de_la_clave():
    clave = generar_clave_acceso(dt.date(2026, 11, 27), "01", "1790012345001", "1", "001", "002", 123, "12345678")
    assert len(clave) == 49 and validar_clave_acceso(clave)
    assert clave[:8] == "27112026"
    assert clave[8:10] == "01"
    assert clave[10:23] == "1790012345001"
    assert clave[23] == "1"
    assert clave[24:30] == "001002"
    assert clave[30:39] == "000000123"
    assert clave[39:47] == "12345678"
    assert clave[47] == "1"


def test_clave_rechaza_datos_mal_formados():
    with pytest.raises(ValueError):
        generar_clave_acceso(dt.date.today(), "01", "179001234500", "1", "001", "001", 1)
    with pytest.raises(ValueError):
        generar_clave_acceso(dt.date.today(), "99", "1790012345001", "1", "001", "001", 1)


def test_clave_alterada_no_valida():
    clave = generar_clave_acceso(dt.date(2026, 1, 2), "04", "1790012345001", "2", "001", "001", 9, "00000001")
    alterada = clave[:30] + ("1" if clave[30] != "1" else "2") + clave[31:]
    assert not validar_clave_acceso(alterada)


@pytest.mark.parametrize("cedula,ok", [("1710034065", True), ("1710034066", False), ("2500000000", False), ("17100340", False)])
def test_cedula(cedula, ok):
    assert validar_cedula(cedula) is ok


@pytest.mark.parametrize(
    "ruc,ok",
    [("1710034065001", True), ("1710034066001", False), ("1790012345001", True), ("1760001550001", True), ("1790012345000", False)],
)
def test_ruc(ruc, ok):
    assert validar_ruc(ruc) is ok
