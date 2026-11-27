# Prueba real con el SRI (cuando tengas la clave del .p12)

Esta prueba manda una factura de USD 1.15 y su nota de crédito al **ambiente de pruebas**
del SRI (`celcer`). No tienen valor legal y no se pueden convertir en facturas reales:
el comando no tiene forma de usar producción.

Tiempo estimado: 15 minutos.

## Antes de empezar

- [ ] Tienes el archivo de firma `.p12` y su contraseña.
- [ ] El `.p12` es del **mismo RUC** que va a facturar.
- [ ] El RUC está habilitado para facturación electrónica en el portal SRI en línea,
      sección de facturación electrónica. El ambiente de pruebas también se solicita ahí.
- [ ] La computadora tiene internet normal (no sirve el servidor de Claude: no llega al SRI).
- [ ] Python 3.10 o más nuevo instalado.

## Paso 1: instalar

En Windows (PowerShell):

```powershell
py -m pip install "sri-core[ride] @ git+https://github.com/JoseTituana/odoo-inventory-sync#subdirectory=packages/sri-core"
```

Mientras el PR no esté fusionado, agrega la rama al final de la URL:
`...odoo-inventory-sync@claude/project-thread-p8uam8#subdirectory=packages/sri-core`.

## Paso 2: revisar la firma

```powershell
sri-core certificado --p12 C:\ruta\firma.p12
```

Pide la contraseña y muestra el titular y la fecha de vencimiento.

- [ ] El titular es el correcto.
- [ ] No está vencida.

Si dice "No se pudo abrir el .p12", la contraseña o el archivo están mal.

## Paso 3: probar todo sin enviar

```powershell
sri-core prueba --p12 C:\ruta\firma.p12 --ruc TU_RUC --razon-social "RAZÓN SOCIAL EXACTA" --dir-matriz "DIRECCIÓN" --solo-firmar
```

- [ ] Dice "Firmado y verificado" y "Solo se firmó (sin enviar)".

## Paso 4: la prueba real

```powershell
sri-core prueba --p12 C:\ruta\firma.p12 --ruc TU_RUC --razon-social "RAZÓN SOCIAL EXACTA" --dir-matriz "DIRECCIÓN"
```

Opciones útiles:

| Opción | Cuándo |
| --- | --- |
| `--obligado-contabilidad` | Si el negocio está obligado a llevar contabilidad. |
| `--rimpe "CONTRIBUYENTE RÉGIMEN RIMPE"` | Si el negocio está en el RIMPE (copia la leyenda exacta de tu RUC). |
| `--estab 002` | Si el establecimiento no es el 001. |
| `--secuencial 2` | Para repetir la prueba: el SRI no acepta dos veces el mismo número. |
| `--sin-nota-credito` | Para probar solo la factura. |

Resultado esperado:

```
LISTO: factura y nota de crédito autorizadas por el SRI. Archivos en prueba-sri
```

En la carpeta `prueba-sri` quedan, para cada comprobante, el XML firmado, el XML autorizado
y el RIDE en PDF.

- [ ] Factura AUTORIZADA.
- [ ] Nota de crédito AUTORIZADA.
- [ ] Abriste los dos PDF y los datos se ven bien.

## Si algo falla

El comando explica los errores más comunes. Los demás:

| Mensaje | Qué hacer |
| --- | --- |
| "Sin conexión con el SRI" | Revisa internet. El SRI a veces se cae; intenta en unos minutos. |
| `EN PROCESO` al final | Normal en horas pico. Espera 5 minutos y corre `sri-core consultar CLAVE`. |
| Error con el RUC o "no habilitado" | Falta solicitar el ambiente de pruebas en el portal del SRI. |
| `[45]` secuencial registrado | Repite con `--secuencial` más alto. |
| Otro | Copia el mensaje completo y mándamelo. |

## Después de la prueba

Cuando pase, la librería está lista para producción. En tu sistema solo cambia
`ambiente=Ambiente.PRODUCCION` en el `Emisor`, y empieza el secuencial de producción en 1
(es una numeración aparte de la de pruebas).
