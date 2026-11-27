"""Ejemplo: cómo conecta un POS nuevo (p. ej. el del restaurante) la librería sri-core.

Muestra las tres cosas que la librería NO hace y tu sistema sí debe hacer:

1. Llevar el secuencial sin saltos ni repetidos (tabla ``secuenciales``).
2. Guardar cada factura ANTES de enviarla, con su clave de acceso, para poder
   reintentar si el SRI o el internet fallan (tabla ``facturas``).
3. Reintentar las pendientes más tarde con la MISMA clave (``reintentar_pendientes``).

Usa SQLite para que se pueda correr tal cual; en un sistema real va la base de datos
del proyecto (PostgreSQL con Django, etc.). La lógica es la misma.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal

from sri_core import (
    ClienteSRI, Comprador, Emisor, Factura, Firmador, Item, Pago, SRIConexionError, comprobante_xml, generar_ride,
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS secuenciales (
    estab TEXT, pto_emi TEXT, tipo TEXT, ultimo INTEGER NOT NULL,
    PRIMARY KEY (estab, pto_emi, tipo)
);
CREATE TABLE IF NOT EXISTS facturas (
    clave_acceso TEXT PRIMARY KEY,
    numero TEXT UNIQUE NOT NULL,
    venta_id TEXT UNIQUE NOT NULL,     -- evita facturar dos veces la misma venta
    total TEXT NOT NULL,
    datos TEXT NOT NULL,               -- la venta en JSON: con esto se rehace la factura idéntica
    estado TEXT NOT NULL,              -- PENDIENTE, AUTORIZADO, NO AUTORIZADO, DEVUELTA
    xml_firmado BLOB NOT NULL,
    xml_autorizado BLOB,
    ride_pdf BLOB,
    mensajes TEXT
);
"""


def _a_json(f: Factura) -> str:
    c = f.comprador
    return json.dumps({
        "secuencial": f.secuencial, "fecha": f.fecha.isoformat(), "codigo_numerico": f.codigo_numerico,
        "comprador": [c.identificacion, c.razon_social, c.email, c.direccion, c.telefono],
        "items": [[i.codigo, i.descripcion, str(i.cantidad), str(i.precio), str(i.iva), str(i.descuento),
                   i.precio_incluye_iva] for i in f.items],
        "pagos": [[p.forma, str(p.total)] for p in f.pagos],
    })


def _de_json(emisor: Emisor, texto: str) -> Factura:
    d = json.loads(texto)
    ident, nombre, email, direccion, telefono = d["comprador"]
    comprador = (Comprador.consumidor_final() if ident == "9999999999999" else
                 Comprador.desde_identificacion(ident, nombre, email=email, direccion=direccion, telefono=telefono))
    items = [Item(c, desc, cant, precio, iva=iva, descuento=dsc, precio_incluye_iva=incl)
             for c, desc, cant, precio, iva, dsc, incl in d["items"]]
    return Factura(emisor, d["secuencial"], comprador, items, fecha=date.fromisoformat(d["fecha"]),
                   codigo_numerico=d["codigo_numerico"], pagos=[Pago(f, t) for f, t in d["pagos"]])


class FacturadorPOS:
    def __init__(self, db: sqlite3.Connection, emisor: Emisor, firmador: Firmador, cliente_sri: ClienteSRI):
        self.db = db
        self.db.executescript(ESQUEMA)
        self.emisor = emisor
        self.firmador = firmador
        self.sri = cliente_sri

    def _siguiente_secuencial(self) -> int:
        """Reserva el siguiente número dentro de la transacción en curso."""
        e = self.emisor
        self.db.execute(
            "INSERT OR IGNORE INTO secuenciales VALUES (?, ?, '01', 0)", (e.estab, e.pto_emi)
        )
        self.db.execute(
            "UPDATE secuenciales SET ultimo = ultimo + 1 WHERE estab=? AND pto_emi=? AND tipo='01'",
            (e.estab, e.pto_emi),
        )
        (n,) = self.db.execute(
            "SELECT ultimo FROM secuenciales WHERE estab=? AND pto_emi=? AND tipo='01'", (e.estab, e.pto_emi)
        ).fetchone()
        return n

    def facturar(self, venta_id: str, comprador: Comprador, items: list[Item], pagos: list[Pago] | None = None) -> dict:
        """Lo que hace el botón "Facturar" del POS."""
        ya = self.db.execute("SELECT clave_acceso FROM facturas WHERE venta_id=?", (venta_id,)).fetchone()
        if ya:  # doble clic o reenvío: no se crea otra factura
            return self.estado(ya[0])

        # 1) Número + XML firmado + registro PENDIENTE en una sola transacción.
        with self.db:
            factura = Factura(self.emisor, self._siguiente_secuencial(), comprador, items, pagos=pagos or [])
            firmado = self.firmador.firmar(comprobante_xml(factura))
            total = factura.calcular()[1].importe_total
            self.db.execute(
                "INSERT INTO facturas (clave_acceso, numero, venta_id, total, datos, estado, xml_firmado) "
                "VALUES (?, ?, ?, ?, ?, 'PENDIENTE', ?)",
                (factura.clave_acceso, factura.numero, venta_id, str(total), _a_json(factura), firmado),
            )
        # 2) Envío: si falla la red, la factura queda PENDIENTE y se reintenta luego.
        self._enviar(factura, firmado)
        return self.estado(factura.clave_acceso)

    def _enviar(self, factura: Factura, firmado: bytes) -> None:
        clave = factura.clave_acceso
        try:
            r = self.sri.enviar_y_autorizar(firmado)
        except SRIConexionError:
            return  # sigue PENDIENTE
        if r.estado == "EN PROCESO":
            return  # sigue PENDIENTE; se consulta en el próximo reintento
        pdf = generar_ride(factura, r) if r.autorizado else None
        with self.db:
            self.db.execute(
                "UPDATE facturas SET estado=?, xml_autorizado=?, ride_pdf=?, mensajes=? WHERE clave_acceso=?",
                (r.estado, r.xml_autorizado() if r.autorizado else None, pdf,
                 "; ".join(str(m) for m in r.mensajes), clave),
            )

    def reintentar_pendientes(self) -> int:
        """Correr cada pocos minutos (tarea programada). Reenvía con la MISMA clave."""
        pendientes = self.db.execute("SELECT datos, xml_firmado FROM facturas WHERE estado='PENDIENTE'").fetchall()
        for datos, firmado in pendientes:
            self._enviar(_de_json(self.emisor, datos), firmado)
        return len(pendientes)

    def estado(self, clave: str) -> dict:
        fila = self.db.execute(
            "SELECT numero, estado, total, mensajes, ride_pdf IS NOT NULL FROM facturas WHERE clave_acceso=?", (clave,)
        ).fetchone()
        return {"clave_acceso": clave, "numero": fila[0], "estado": fila[1], "total": Decimal(fila[2]),
                "mensajes": fila[3], "tiene_pdf": bool(fila[4])}
