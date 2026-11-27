# sri-core

Facturación electrónica del SRI de Ecuador como una pieza que se integra en cualquier
proyecto Python (Odoo, Django, Flask, un script). No depende de ningún framework ni de
una base de datos: recibe los datos de la venta y devuelve el XML firmado, la respuesta
del SRI y el RIDE en PDF.

Hace cinco cosas:

1. **Clave de acceso** de 49 dígitos con su dígito verificador (módulo 11).
2. **XML** de factura y nota de crédito, versión 1.1.0, validado contra el XSD oficial.
3. **Firma XAdES-BES** con el `.p12` del negocio (RSA-SHA1, la que pide el SRI).
4. **Envío y autorización** con los servicios web del SRI, en pruebas (`celcer`) o producción (`cel`).
5. **RIDE**, la factura en PDF para imprimir o enviar al cliente.

## Instalación

Desde este repositorio (mientras no tenga repositorio propio):

```bash
pip install "sri-core[ride] @ git+https://github.com/JoseTituana/odoo-inventory-sync#subdirectory=packages/sri-core"
```

`[ride]` agrega `reportlab` para el PDF. Sin él funciona todo menos `generar_ride`.

## Ejemplo completo

```python
from datetime import date
from sri_core import (
    Ambiente, ClienteSRI, Comprador, Emisor, Factura, Firmador, Item, Pago,
    comprobante_xml, generar_ride,
)

emisor = Emisor(
    ruc="1790012345001",
    razon_social="CALZADO JOSHEPS",
    dir_matriz="Av. Principal 123, Quito",
    estab="001", pto_emi="001",
    obligado_contabilidad=False,
    ambiente=Ambiente.PRUEBAS,          # cambiar a PRODUCCION cuando todo esté probado
)

factura = Factura(
    emisor,
    secuencial=1,                        # tu sistema lleva el contador, sin saltos ni repetidos
    comprador=Comprador.desde_identificacion("1710034065", "Ana Pérez", email="ana@correo.com"),
    items=[Item("ZAP-38", "Botín negro 38", 1, "45.00", precio_incluye_iva=True)],
    pagos=[Pago("efectivo", "45.00")],   # si no lo pones, asume efectivo por el total
)

xml = comprobante_xml(factura)
firmado = Firmador.desde_p12("firma.p12", "contraseña").firmar(xml)
respuesta = ClienteSRI(emisor.ambiente).enviar_y_autorizar(firmado)

if respuesta.autorizado:
    pdf = generar_ride(factura, respuesta)
    # guarda: factura.clave_acceso, respuesta.xml_autorizado(), pdf
else:
    for m in respuesta.mensajes:
        print(m)                         # p. ej. "[39] FIRMA INVALIDA"
```

## Guías

- [Cómo conectar sri-core a un sistema nuevo](docs/integracion.md), con un
  [ejemplo de POS](ejemplos/pos_minimo.py) que también se prueba automáticamente.
- [Prueba real con el SRI](docs/prueba-real-sri.md): los pasos para cuando tengas la clave del `.p12`.

## Comando `sri-core`

| Comando | Para qué |
| --- | --- |
| `sri-core certificado --p12 firma.p12` | Ver titular y vencimiento de la firma |
| `sri-core prueba --p12 firma.p12 --ruc ... --razon-social ... --dir-matriz ...` | Factura y nota de crédito reales en el ambiente de **pruebas** del SRI |
| `sri-core consultar CLAVE` | Consultar si una clave de acceso quedó autorizada |

La contraseña se pide en pantalla o se toma de la variable `SRI_P12_CLAVE`.

## Lo que tu sistema tiene que guardar

La librería no guarda nada; eso le toca al proyecto que la usa:

| Dato | Por qué |
| --- | --- |
| Secuencial por establecimiento y punto de emisión | El SRI rechaza números repetidos; un salto confunde a la contadora. |
| `factura.clave_acceso` (y `codigo_numerico`) | Si el SRI no responde, se vuelve a consultar con la misma clave. **No** se crea otra factura. |
| `respuesta.estado` | `AUTORIZADO`, `NO AUTORIZADO`, `DEVUELTA` (no pasó la recepción) o `EN PROCESO` (consultar más tarde con `ClienteSRI.autorizacion(clave)`). |
| `respuesta.xml_autorizado()` y el PDF | Se entregan al cliente y se archivan 7 años. |

## Precios con o sin IVA

- `precio_incluye_iva=True`: el precio es el de percha (como en Josheps). El total de la factura
  queda exactamente igual al precio de percha y la librería separa base e IVA.
- `precio_incluye_iva=False`: el precio es sin IVA y el IVA se suma encima.

Tarifas de IVA válidas: `15`, `0`, `5`, `12`, `13`, `14`, `"no_objeto"`, `"exento"`.

## Reglas del SRI que la librería ya revisa

- Consumidor final (`9999999999999`) solo hasta USD 50 por factura.
- No hay notas de crédito a consumidor final.
- Los pagos deben sumar el total de la factura.
- Cédula con dígito verificador válido; RUC con formato válido.
- La firma vencida no se usa (salvo `permitir_vencido=True`, solo para pruebas).

## RUC del proveedor de software

Si comercializas el sistema, la normativa del SRI pide identificar al proveedor. Se agrega como
campo adicional:

```python
Factura(..., info_adicional={"RUC Proveedor": "1790000000001"})
```

## Pruebas

```bash
cd packages/sri-core
pip install -e ".[dev]"
pytest -q
```

Las pruebas usan un certificado de prueba generado al vuelo, validan el XML contra el XSD oficial y,
si `xmlsec1` está instalado, comprueban la firma con esa herramienta independiente. Lo único que no
se puede probar sin una firma real es la respuesta del SRI: para eso hay que enviar una factura al
ambiente de pruebas con el `.p12` del negocio.

## Pendiente

- Nota de débito, comprobante de retención, guía de remisión y liquidación de compra.
- Probar contra el ambiente de pruebas del SRI con un `.p12` real (ver [la guía](docs/prueba-real-sri.md)).
- Envío del RIDE y el XML por correo (lo hace el proyecto que usa la librería).
