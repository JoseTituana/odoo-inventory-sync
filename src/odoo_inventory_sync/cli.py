"""Command line entry point."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from .catalog import CatalogError, build_plans, file_saved_at, load_catalog
from .catalog_sync import (CatalogSyncError, OdooCatalogGateway, changes_since_count,
                           import_catalog)
from .client import OdooClient, OdooError
from .labels import PROFILES, expand, pages_needed, read_stock_labels, render_pdf
from .sync import load_rows, sync_products


# argparse's own texts (usage, errors, -h) in Spanish; unknown ones stay as they are.
_ARGPARSE_ES = {
    "usage: ": "uso: ",
    "positional arguments": "argumentos",
    "options": "opciones",
    "optional arguments": "opciones",
    "show this help message and exit": "mostrar esta ayuda y salir",
    "the following arguments are required: %s": "faltan estos argumentos: %s",
    "unrecognized arguments: %s": "argumentos no reconocidos: %s",
    "%(prog)s: error: %(message)s\n": "%(prog)s: error: %(message)s\n",
    "argument %(argument_name)s: %(message)s": "argumento %(argument_name)s: %(message)s",
    "invalid choice: %(value)r (choose from %(choices)s)":
        "opción no válida: %(value)r (elige entre %(choices)s)",
    "invalid %(type)s value: %(value)r": "valor %(type)s no válido: %(value)r",
    "expected one argument": "se esperaba un valor",
}
argparse._ = lambda text: _ARGPARSE_ES.get(text, text)


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines into the environment without overriding it."""
    env_file = Path(path)
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odoo-sync", description="Sincroniza un CSV de productos con Odoo."
    )
    parser.add_argument("csv_path", help="CSV con las columnas: sku,name,price,qty")
    parser.add_argument("--dry-run", action="store_true", help="simular: mostrar los cambios sin escribir nada")
    parser.add_argument(
        "--stock-location-id",
        type=int,
        help="id de la ubicación de stock de Odoo; si se indica, también se ajusta el stock",
    )
    return parser


ENV_KEYS = ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_PASSWORD")


def _client_from_env() -> OdooClient | None:
    load_dotenv()
    missing = [k for k in ENV_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Faltan variables de entorno: {', '.join(missing)}", file=sys.stderr)
        return None
    return OdooClient(*(os.environ[k] for k in ENV_KEYS))


def build_catalog_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odoo-sync catalog",
        description="Importa un catálogo de calzado (modelo x color x material x talla) como "
        "variantes de producto con código, código de barras EAN-13 interno y stock.",
    )
    parser.add_argument("path", help=".xlsx o .csv con las columnas Modelo, Color, Material, "
                        "Talla, Precio, Costo, Cantidad y opcionalmente Categoria")
    parser.add_argument("--dry-run", action="store_true",
                        help="simular: mostrar los cambios sin escribir nada")
    parser.add_argument("--location-id", type=int,
                        help="id de la ubicación del stock (por defecto: la bodega principal)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="mostrar cada cambio de stock")
    parser.add_argument("--counted-at", metavar="'YYYY-MM-DD HH:MM'",
                        help="cuándo se hizo el conteo, en hora local (por defecto: cuando se "
                        "guardó el archivo); se avisa de los movimientos posteriores")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="aplicar aunque haya movimientos después del conteo, sin preguntar")
    return parser


def _parse_counted_at(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).astimezone()  # local time
        except ValueError:
            continue
    raise ValueError(f"--counted-at debe tener la forma '2026-11-27 21:30', no {text!r}")


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [s/N] ").strip().lower() in ("y", "yes", "s", "si", "sí")
    except EOFError:
        return False


def catalog_main(argv: list[str]) -> int:
    args = build_catalog_parser().parse_args(argv)
    try:
        rows, errors = load_catalog(args.path)
        plans, plan_errors = build_plans(rows)
        counted_at = (_parse_counted_at(args.counted_at) if args.counted_at
                      else file_saved_at(args.path))
    except (OSError, CatalogError, ValueError) as error:
        print(f"No se puede leer el catálogo: {error}", file=sys.stderr)
        return 2
    errors += plan_errors
    if errors:
        # All or nothing: a partial catalog would leave stock half counted.
        for message in errors:
            print(f"error {message}", file=sys.stderr)
        print(f"{len(errors)} error(es) en el archivo; no se importó nada.", file=sys.stderr)
        return 2
    pairs = sum(p.pairs for p in plans)
    variants = sum(p.variant_count for p in plans)
    print(f"leídas {len(rows)} filas: {len(plans)} modelos, {variants} variantes, {pairs} pares")

    client = _client_from_env()
    if client is None:
        return 2
    try:
        client.authenticate()
        gateway = OdooCatalogGateway(client)
        moved = changes_since_count(gateway, plans, counted_at, args.location_id)
        if moved:
            local = counted_at.astimezone().strftime("%Y-%m-%d %H:%M")
            print(f"aviso: hubo movimientos de stock después del conteo ({local}, "
                  f"{'--counted-at' if args.counted_at else 'cuando se guardó el archivo'}); "
                  "importar desharía estos movimientos:", file=sys.stderr)
            for line in moved:
                print(f"  {line}", file=sys.stderr)
            if not args.dry_run and not args.yes and not _confirm("¿Aplicar el conteo de todas formas?"):
                print("No se importó nada. Vuelve a contar, o usa --counted-at / --yes.",
                      file=sys.stderr)
                return 1
        result = import_catalog(gateway, plans, dry_run=args.dry_run,
                                location_id=args.location_id)
    except (OdooError, CatalogSyncError) as error:
        print(f"Error de Odoo: {error}", file=sys.stderr)
        return 1

    prefix = "[simulación] " if args.dry_run else ""
    print(prefix + result.summary())
    for label, items in (("atributo nuevo", result.attributes_created),
                         ("valor nuevo", result.values_created),
                         ("categoría nueva", result.categories_created),
                         ("modelo nuevo", result.models_created),
                         ("modelo actualizado", result.models_updated)):
        for item in items:
            print(f"  {label}: {item}")
    if args.verbose:
        for change in result.stock_changes:
            print(f"  stock {change}")
    for message in result.errors:
        print(f"error {message}", file=sys.stderr)
    return 1 if result.errors else 0


def build_labels_parser() -> argparse.ArgumentParser:
    profiles = "\n".join(f"  {p.name}: {p.description}" for p in PROFILES.values())
    parser = argparse.ArgumentParser(
        prog="odoo-sync labels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Imprime una etiqueta por cada par en stock, en un PDF.",
        epilog=f"perfiles:\n{profiles}\n\nImprime el PDF al 100 % (tamaño real), no 'ajustar a la "
               "página'.",
    )
    parser.add_argument("--profile", default="a4-21", choices=sorted(PROFILES))
    parser.add_argument("-o", "--output", help="archivo PDF (por defecto: labels-<perfil>-<hora>.pdf)")
    parser.add_argument("--model", help="solo los modelos cuyo nombre contiene este texto")
    parser.add_argument("--category", help="solo las categorías que contienen este texto")
    parser.add_argument("--location-id", type=int,
                        help="id de la ubicación del stock (por defecto: la bodega principal)")
    parser.add_argument("--start-at", type=int, default=1,
                        help="primera posición libre en una hoja ya usada (1 = arriba a la izquierda)")
    parser.add_argument("--brand", help="texto impreso arriba de cada etiqueta")
    parser.add_argument("--outlines", action="store_true",
                        help="dibujar el borde de las etiquetas (para una prueba en papel normal)")
    parser.add_argument("--dry-run", action="store_true", help="contar las etiquetas sin crear el PDF")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostrar las etiquetas de cada variante")
    return parser


def labels_main(argv: list[str]) -> int:
    args = build_labels_parser().parse_args(argv)
    profile = PROFILES[args.profile]
    if not 1 <= args.start_at <= profile.per_page:
        print(f"--start-at debe estar entre 1 y {profile.per_page} para {profile.name}",
              file=sys.stderr)
        return 2
    client = _client_from_env()
    if client is None:
        return 2
    try:
        client.authenticate()
        stock = read_stock_labels(client, args.location_id, args.model, args.category)
    except (OdooError, ValueError) as error:
        print(f"Error de Odoo: {error}", file=sys.stderr)
        return 1
    plan = expand(stock)
    pages = pages_needed(len(plan.labels), profile, args.start_at)
    prefix = "[simulación] " if args.dry_run else ""
    print(f"{prefix}{plan.summary()} -> {pages} página(s) en {profile.name}")
    if args.verbose:
        counts: dict[str, int] = {}
        for label in plan.labels:
            name = label.sku or label.barcode
            counts[name] = counts.get(name, 0) + 1
        for name, count in counts.items():
            print(f"  {count:>3} x {name}")
    for message in plan.skipped:
        print(f"aviso {message}", file=sys.stderr)
    if not plan.labels:
        filters = [f"{flag} {value!r}" for flag, value in
                   (("--model", args.model), ("--category", args.category)) if value]
        reason = (f"ninguna variante con stock coincide con {' y '.join(filters)}"
                  if filters else "no hay variantes con stock en esa ubicación")
        print(f"No hay etiquetas que imprimir: {reason}.", file=sys.stderr)
    if args.dry_run or not plan.labels:
        return 0
    output = args.output or f"labels-{profile.name}-{datetime.now():%Y%m%d-%H%M}.pdf"
    try:
        render_pdf(plan.labels, profile, output, start_at=args.start_at, brand=args.brand,
                   outlines=args.outlines)
    except ValueError as error:
        print(f"No se puede imprimir: {error}", file=sys.stderr)
        return 1
    print(f"creado {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "catalog":
        return catalog_main(argv[1:])
    if argv and argv[0] == "labels":
        return labels_main(argv[1:])
    args = build_parser().parse_args(argv)
    load_dotenv()

    missing = [k for k in ENV_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Faltan variables de entorno: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        rows, csv_errors = load_rows(args.csv_path)
    except (OSError, ValueError) as error:
        print(f"No se puede leer el CSV: {error}", file=sys.stderr)
        return 2

    for message in csv_errors:
        print(f"omitido {message}", file=sys.stderr)

    client = OdooClient(
        os.environ["ODOO_URL"],
        os.environ["ODOO_DB"],
        os.environ["ODOO_USER"],
        os.environ["ODOO_PASSWORD"],
    )
    try:
        client.authenticate()  # fail fast on bad credentials instead of once per row
        result = sync_products(
            client, rows, dry_run=args.dry_run, stock_location_id=args.stock_location_id
        )
    except OdooError as error:
        print(f"Error de Odoo: {error}", file=sys.stderr)
        return 1

    prefix = "[simulación] " if args.dry_run else ""
    print(prefix + result.summary())
    for message in result.errors:
        print(f"error {message}", file=sys.stderr)
    return 1 if (result.errors or csv_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
