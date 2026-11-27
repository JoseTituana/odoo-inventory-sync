"""Comando ``sri-core`` para revisar la firma y hacer la prueba real con el SRI.

    sri-core certificado --p12 firma.p12
    sri-core prueba --p12 firma.p12 --ruc 1790012345001 --razon-social "MI NEGOCIO" --dir-matriz "Quito"
    sri-core consultar 2711202601179001234500110010010000001231234567811

La contraseña del .p12 se pide en pantalla (no queda en el historial) o se
toma de la variable de entorno SRI_P12_CLAVE.

``prueba`` SIEMPRE usa el ambiente de pruebas del SRI: no tiene valor legal
y no puede emitir una factura real por error.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import date
from pathlib import Path

from .catalogos import Ambiente
from .firma import FirmaError, Firmador, verificar_firma
from .modelos import Comprador, Emisor, Factura, Item, NotaCredito
from .ride import generar_ride
from .sri_cliente import ClienteSRI, RespuestaAutorizacion, SRIConexionError
from .xml_comprobantes import comprobante_xml

# Explicación en palabras simples de los mensajes más comunes del SRI.
AYUDA_MENSAJES = {
    "35": "El XML no cumple la estructura del SRI. Revisa los datos del emisor (RUC, direcciones).",
    "39": "Firma inválida. Revisa que el .p12 sea el del RUC que factura y que no esté vencido.",
    "43": "Esa clave de acceso ya se había enviado. Consulta su estado con 'sri-core consultar'.",
    "45": "Ese secuencial ya se usó. Vuelve a probar con un --secuencial más alto.",
    "65": "Fecha de emisión fuera de plazo. La factura debe enviarse con la fecha de hoy.",
    "70": "El SRI todavía está procesando. Consulta más tarde con 'sri-core consultar'.",
}


def _clave_p12(args) -> str:
    return os.environ.get("SRI_P12_CLAVE") or getpass.getpass("Contraseña del .p12: ")


def _abrir_firma(args) -> Firmador:
    ruta = Path(args.p12)
    if not ruta.is_file():
        raise FirmaError(f"No existe el archivo de firma: {ruta}")
    return Firmador.desde_p12(ruta, _clave_p12(args))


def _imprimir_respuesta(r: RespuestaAutorizacion, out) -> None:
    print(f"  Estado: {r.estado}", file=out)
    if r.numero_autorizacion:
        print(f"  Número de autorización: {r.numero_autorizacion}", file=out)
    if r.fecha_autorizacion:
        print(f"  Fecha de autorización: {r.fecha_autorizacion:%d/%m/%Y %H:%M:%S}", file=out)
    for m in r.mensajes:
        print(f"  SRI dice: {m}", file=out)
        if m.identificador in AYUDA_MENSAJES:
            print(f"    -> {AYUDA_MENSAJES[m.identificador]}", file=out)


def cmd_certificado(args, out=sys.stdout) -> int:
    info = _abrir_firma(args).info()
    print(f"Titular:      {info.titular}", file=out)
    print(f"Emitida por:  {info.emisor}", file=out)
    print(f"Válida desde: {info.valido_desde:%d/%m/%Y}", file=out)
    print(f"Vence:        {info.valido_hasta:%d/%m/%Y} (faltan {info.dias_para_vencer} días)", file=out)
    if info.dias_para_vencer < 30:
        print("ATENCIÓN: renueva la firma pronto; vencida, el SRI rechaza todo.", file=out)
    return 0


def _emitir(c, firmador: Firmador, cliente: ClienteSRI, carpeta: Path, nombre: str, out, solo_firmar: bool):
    xml = comprobante_xml(c)
    firmado = firmador.firmar(xml)
    if not verificar_firma(firmado):  # no debería pasar nunca; si pasa, no se envía nada
        raise FirmaError("La firma no se pudo verificar localmente")
    (carpeta / f"{nombre}-firmado.xml").write_bytes(firmado)
    print(f"  Firmado y verificado: {carpeta / f'{nombre}-firmado.xml'}", file=out)
    print(f"  Clave de acceso: {c.clave_acceso}", file=out)
    if solo_firmar:
        return None
    r = cliente.enviar_y_autorizar(firmado)
    _imprimir_respuesta(r, out)
    try:
        (carpeta / f"{nombre}-RIDE.pdf").write_bytes(generar_ride(c, r))
    except ImportError:
        print("  (Sin PDF: instala sri-core[ride] para generar el RIDE)", file=out)
    if r.autorizado:
        (carpeta / f"{nombre}-autorizado.xml").write_bytes(r.xml_autorizado())
    return r


def cmd_prueba(args, out=sys.stdout, cliente: ClienteSRI | None = None) -> int:
    firmador = _abrir_firma(args)
    emisor = Emisor(
        ruc=args.ruc,
        razon_social=args.razon_social,
        dir_matriz=args.dir_matriz,
        estab=args.estab,
        pto_emi=args.pto_emi,
        obligado_contabilidad=args.obligado_contabilidad,
        contribuyente_rimpe=args.rimpe,
        ambiente=Ambiente.PRUEBAS,
    )
    comprador = Comprador.desde_identificacion(args.cliente_id or args.ruc, args.cliente_nombre or args.razon_social)
    carpeta = Path(args.salida)
    carpeta.mkdir(parents=True, exist_ok=True)
    cliente = cliente or ClienteSRI(Ambiente.PRUEBAS)
    hoy = date.today()

    print("1) Factura de prueba (ambiente de PRUEBAS, sin valor legal)", file=out)
    factura = Factura(
        emisor, args.secuencial, comprador,
        [Item("PRUEBA-1", "Producto de prueba sri-core", 1, "1.15", precio_incluye_iva=True)],
        fecha=hoy, info_adicional={"Nota": "Prueba de integración sri-core"},
    )
    r = _emitir(factura, firmador, cliente, carpeta, f"factura-{factura.numero}", out, args.solo_firmar)
    if args.solo_firmar:
        print("\nSolo se firmó (sin enviar). Todo lo local funciona.", file=out)
        return 0
    if not r.autorizado:
        print("\nLa factura no se autorizó. Revisa los mensajes de arriba.", file=out)
        return 1
    if args.sin_nota_credito:
        print("\nLISTO: factura autorizada por el SRI.", file=out)
        return 0

    print("\n2) Nota de crédito que anula la factura de prueba", file=out)
    nc = NotaCredito(
        emisor, args.secuencial, comprador,
        [Item("PRUEBA-1", "Producto de prueba sri-core", 1, "1.15", precio_incluye_iva=True)],
        fecha=hoy, factura_numero=factura.numero, factura_fecha=hoy, motivo="Prueba de integración sri-core",
    )
    r_nc = _emitir(nc, firmador, cliente, carpeta, f"nota-credito-{nc.numero}", out, False)
    if not r_nc.autorizado:
        print("\nLa nota de crédito no se autorizó. Revisa los mensajes de arriba.", file=out)
        return 1
    print(f"\nLISTO: factura y nota de crédito autorizadas por el SRI. Archivos en {carpeta}", file=out)
    return 0


def cmd_consultar(args, out=sys.stdout, cliente: ClienteSRI | None = None) -> int:
    cliente = cliente or ClienteSRI(Ambiente.PRODUCCION if args.produccion else Ambiente.PRUEBAS)
    r = cliente.autorizacion(args.clave)
    _imprimir_respuesta(r, out)
    return 0 if r.autorizado else 1


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sri-core", description="Herramientas de facturación electrónica del SRI.")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("certificado", help="Muestra titular y vencimiento del .p12")
    c.add_argument("--p12", required=True, help="Ruta del archivo de firma .p12")
    c.set_defaults(func=cmd_certificado)

    pr = sub.add_parser("prueba", help="Factura y nota de crédito reales en el ambiente de PRUEBAS del SRI")
    pr.add_argument("--p12", required=True)
    pr.add_argument("--ruc", required=True, help="RUC del negocio, el mismo de la firma")
    pr.add_argument("--razon-social", required=True)
    pr.add_argument("--dir-matriz", required=True)
    pr.add_argument("--estab", default="001")
    pr.add_argument("--pto-emi", default="999", help="Punto de emisión para pruebas (por defecto 999)")
    pr.add_argument("--secuencial", type=int, default=1)
    pr.add_argument("--obligado-contabilidad", action="store_true")
    pr.add_argument("--rimpe", help='Leyenda RIMPE, p. ej. "CONTRIBUYENTE RÉGIMEN RIMPE"')
    pr.add_argument("--cliente-id", help="Cédula o RUC del comprador (por defecto, el mismo RUC)")
    pr.add_argument("--cliente-nombre")
    pr.add_argument("--salida", default="prueba-sri", help="Carpeta donde se guardan XML y PDF")
    pr.add_argument("--solo-firmar", action="store_true", help="Firma sin enviar al SRI")
    pr.add_argument("--sin-nota-credito", action="store_true")
    pr.set_defaults(func=cmd_prueba)

    co = sub.add_parser("consultar", help="Consulta la autorización de una clave de acceso")
    co.add_argument("clave")
    co.add_argument("--produccion", action="store_true", help="Consultar en producción (por defecto, pruebas)")
    co.set_defaults(func=cmd_consultar)
    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FirmaError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.", file=sys.stderr)
        return 130
    except SRIConexionError as exc:
        print(f"Sin conexión con el SRI: {exc}\nRevisa internet; el SRI a veces está caído, intenta en unos minutos.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
