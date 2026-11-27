`factura_V1.1.0.xsd` y `NotaCredito_V1.1.0.xsd` son los esquemas oficiales que publica
el SRI en su ficha técnica de comprobantes electrónicos (copia tomada del módulo OCA
`l10n_ec_account_edi` 19.0). Sirven para comprobar en las pruebas que el XML tiene la
estructura que el SRI acepta.

`xmldsig-core-schema.xsd` es un esquema mínimo hecho para las pruebas: deja pasar la
firma sin revisarla, porque la firma se comprueba aparte.
