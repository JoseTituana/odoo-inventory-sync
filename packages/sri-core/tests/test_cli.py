import io
from datetime import date

import pytest

from sri_core import ClienteSRI, verificar_firma
from sri_core.cli import cmd_certificado, cmd_consultar, cmd_prueba, construir_parser, main

from sri_falso import SRIQueAutoriza, SesionFalsa, autorizacion, devuelta


@pytest.fixture
def archivo_p12(p12, tmp_path, monkeypatch):
    ruta = tmp_path / "firma.p12"
    ruta.write_bytes(p12)
    monkeypatch.setenv("SRI_P12_CLAVE", "clave123")
    return ruta


def args_prueba(archivo_p12, tmp_path, *extra):
    return construir_parser().parse_args([
        "prueba", "--p12", str(archivo_p12), "--ruc", "1790012345001", "--razon-social", "MI NEGOCIO",
        "--dir-matriz", "Quito", "--salida", str(tmp_path / "salida"), *extra,
    ])


def test_certificado(archivo_p12):
    out = io.StringIO()
    assert cmd_certificado(construir_parser().parse_args(["certificado", "--p12", str(archivo_p12)]), out) == 0
    assert "FIRMA DE PRUEBA" in out.getvalue() and "Vence" in out.getvalue()


def test_prueba_completa_factura_y_nota_credito(archivo_p12, tmp_path):
    sesion = SRIQueAutoriza()
    out = io.StringIO()
    codigo = cmd_prueba(args_prueba(archivo_p12, tmp_path), out, ClienteSRI("1", sesion=sesion))
    texto = out.getvalue()
    assert codigo == 0, texto
    assert "LISTO: factura y nota de crédito autorizadas" in texto
    salida = tmp_path / "salida"
    nombres = sorted(p.name for p in salida.iterdir())
    assert nombres == [
        "factura-001-999-000000001-RIDE.pdf", "factura-001-999-000000001-autorizado.xml",
        "factura-001-999-000000001-firmado.xml", "nota-credito-001-999-000000001-RIDE.pdf",
        "nota-credito-001-999-000000001-autorizado.xml", "nota-credito-001-999-000000001-firmado.xml",
    ]
    assert verificar_firma((salida / "factura-001-999-000000001-firmado.xml").read_bytes())
    # Siempre al ambiente de pruebas, nunca a producción.
    assert all(url.startswith("https://celcer.sri.gob.ec/") for url, _ in sesion.llamadas)
    assert len(sesion.llamadas) == 4


def test_prueba_solo_firmar_no_usa_internet(archivo_p12, tmp_path):
    sesion = SRIQueAutoriza(caido=True)
    out = io.StringIO()
    assert cmd_prueba(args_prueba(archivo_p12, tmp_path, "--solo-firmar"), out, ClienteSRI(sesion=sesion)) == 0
    assert sesion.llamadas == []
    assert "Solo se firmó" in out.getvalue()


def test_prueba_devuelta_explica_el_error(archivo_p12, tmp_path):
    sesion = SesionFalsa([devuelta("39", "FIRMA INVALIDA")])
    out = io.StringIO()
    assert cmd_prueba(args_prueba(archivo_p12, tmp_path), out, ClienteSRI(sesion=sesion)) == 1
    assert "[39] FIRMA INVALIDA" in out.getvalue()
    assert "Revisa que el .p12 sea el del RUC" in out.getvalue()


def test_consultar(archivo_p12):
    clave = "2711202601179001234500110010010000001231234567811"
    out = io.StringIO()
    args = construir_parser().parse_args(["consultar", clave])
    assert cmd_consultar(args, out, ClienteSRI(sesion=SesionFalsa([autorizacion(clave)]))) == 0
    assert "AUTORIZADO" in out.getvalue()


def test_main_contrasena_incorrecta(archivo_p12, monkeypatch, capsys):
    monkeypatch.setenv("SRI_P12_CLAVE", "mala")
    assert main(["certificado", "--p12", str(archivo_p12)]) == 2
    assert "contraseña" in capsys.readouterr().err


def test_main_sin_conexion(archivo_p12, tmp_path, monkeypatch, capsys):
    import sri_core.cli as cli
    monkeypatch.setattr(cli, "ClienteSRI", lambda *a, **k: ClienteSRI(sesion=SRIQueAutoriza(caido=True)))
    codigo = main(["prueba", "--p12", str(archivo_p12), "--ruc", "1790012345001", "--razon-social", "X",
                   "--dir-matriz", "Quito", "--salida", str(tmp_path / "s")])
    assert codigo == 3
    assert "Sin conexión con el SRI" in capsys.readouterr().err
