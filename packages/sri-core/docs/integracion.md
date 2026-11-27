# Cómo conectar sri-core a un sistema nuevo

Guía para agregar facturación electrónica a un proyecto Python, por ejemplo el POS del
restaurante. El ejemplo completo y probado está en
[`ejemplos/pos_minimo.py`](../ejemplos/pos_minimo.py).

## La idea en una frase

Tu sistema vende y guarda; la librería arma, firma, envía y dibuja el PDF.

| Lo hace la librería | Lo hace tu sistema |
| --- | --- |
| Clave de acceso, XML, firma, envío al SRI, RIDE | Guardar el `.p12` y su contraseña de forma segura |
| Calcular base, IVA y totales | Llevar el secuencial sin saltos ni repetidos |
| Revisar reglas del SRI (consumidor final, pagos) | Guardar cada factura **antes** de enviarla |
| Explicar por qué el SRI rechazó algo | Reintentar las pendientes si no hubo internet |
|  | Enviar el PDF y el XML al cliente por correo |

## Paso 1: instalar

```bash
pip install "sri-core[ride] @ git+https://github.com/JoseTituana/odoo-inventory-sync#subdirectory=packages/sri-core"
```

En el `requirements.txt` del proyecto va esa misma línea.

## Paso 2: configurar el negocio una sola vez

```python
from sri_core import Ambiente, Emisor, Firmador

emisor = Emisor(
    ruc="1790012345001",
    razon_social="RESTAURANTE EJEMPLO S.A.",
    nombre_comercial="El Buen Sabor",
    dir_matriz="Av. Principal 123, Quito",
    estab="001", pto_emi="001",           # un pto_emi distinto por cada caja
    obligado_contabilidad=False,
    contribuyente_rimpe="CONTRIBUYENTE RÉGIMEN RIMPE",  # solo si aplica
    ambiente=Ambiente.PRUEBAS,
)
firmador = Firmador.desde_p12("/ruta/segura/firma.p12", os.environ["SRI_P12_CLAVE"])
```

Dónde guardar la firma:

- El `.p12` fuera de la carpeta del código y fuera de Git (el repositorio ya ignora `*.p12`).
- La contraseña en una variable de entorno o en el gestor de secretos del servidor,
  nunca en el código.
- Revisa `firmador.info().dias_para_vencer` al arrancar y avisa si faltan menos de 30 días.

## Paso 3: el botón "Facturar"

```python
from sri_core import Comprador, Factura, Item, Pago, comprobante_xml

comprador = Comprador.desde_identificacion("1710034065", "Ana Pérez", email="ana@correo.com")
# o Comprador.consumidor_final() para ventas de hasta USD 50

items = [
    Item("ALM-01", "Almuerzo ejecutivo", 2, "3.50", precio_incluye_iva=True),
    Item("JUG-02", "Jugo natural", 1, "1.50", precio_incluye_iva=True),
]
factura = Factura(emisor, secuencial=siguiente_numero(), comprador=comprador, items=items,
                  pagos=[Pago("tarjeta_credito", "8.50")], propina="0.85")
firmado = firmador.firmar(comprobante_xml(factura))
guardar_como_pendiente(factura, firmado)     # ANTES de enviar
```

Formas de pago: `efectivo`, `tarjeta_debito`, `tarjeta_credito`, `transferencia`,
`dinero_electronico`, `tarjeta_prepago`, `compensacion`, `endoso_titulos`.

Propina: la del 10 % en restaurantes va en `propina` y no lleva IVA.

## Paso 4: enviar y guardar la respuesta

```python
from sri_core import ClienteSRI, SRIConexionError, generar_ride

sri = ClienteSRI(emisor.ambiente)
try:
    r = sri.enviar_y_autorizar(firmado)
except SRIConexionError:
    return  # queda PENDIENTE; la tarea de reintentos la enviará luego

if r.autorizado:
    guardar(factura.clave_acceso, estado="AUTORIZADO", xml=r.xml_autorizado(), pdf=generar_ride(factura, r))
elif r.estado == "EN PROCESO":
    pass  # sigue PENDIENTE; consultar luego con sri.autorizacion(clave)
else:
    guardar(factura.clave_acceso, estado=r.estado, mensajes=[str(m) for m in r.mensajes])
```

Estados posibles:

| Estado | Qué significa | Qué hacer |
| --- | --- | --- |
| `AUTORIZADO` | Factura válida | Entregar PDF y XML al cliente |
| `EN PROCESO` | El SRI no terminó | Consultar más tarde con la misma clave |
| `DEVUELTA` | No pasó la recepción (estructura, firma, datos) | Leer `mensajes`, corregir y volver a emitir |
| `NO AUTORIZADO` | Se recibió pero fue rechazada | Leer `mensajes`, corregir y volver a emitir |

## Paso 5: reintentos

Corre cada 5 o 10 minutos (cron, Celery, una tarea de Django):

- Toma las facturas `PENDIENTE`.
- Reenvía el **mismo** XML firmado. Nunca lo vuelvas a generar con otra clave:
  podría quedar una factura duplicada.
- Si el SRI responde "clave ya registrada", la librería consulta la autorización sola.

Para poder hacer el RIDE de una factura reintentada, el ejemplo guarda los datos de la venta en
JSON y rehace la `Factura` con el mismo `codigo_numerico`, que da la misma clave de acceso.

## Paso 6: anular con nota de crédito

```python
from sri_core import NotaCredito

nc = NotaCredito(emisor, secuencial=siguiente_numero_nc(), comprador=comprador, items=items_devueltos,
                 factura_numero="001-001-000000123", factura_fecha=fecha_factura, motivo="Devolución")
```

Las notas de crédito tienen su propia numeración y no se pueden emitir a consumidor final.

## Lista para dar por terminada la integración

- [ ] El secuencial se reserva dentro de una transacción de la base de datos.
- [ ] Cada factura se guarda como `PENDIENTE` antes de enviarla.
- [ ] La misma venta no se puede facturar dos veces (doble clic).
- [ ] Hay una tarea que reintenta las pendientes.
- [ ] El `.p12` y su contraseña no están en el código ni en Git.
- [ ] Se avisa cuando la firma está por vencer.
- [ ] Se guardan XML autorizado y PDF por 7 años.
- [ ] Pasó la [prueba real con el SRI](prueba-real-sri.md) en el ambiente de pruebas.
