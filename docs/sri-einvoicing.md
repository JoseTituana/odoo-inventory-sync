# Electronic invoices with the SRI (Ecuador)

How the point of sale issues Ecuadorian electronic invoices without a paid provider, and how to roll it out: staging first, against the SRI test servers, then production.

## How it fits together

| Piece | Where | What it does |
|---|---|---|
| `l10n_ec` | Odoo Community | Ecuadorian chart of accounts, taxes, document types, identification types. Already installed in `jats`. |
| `l10n_ec_base`, `l10n_ec_account_edi` | [OCA l10n-ecuador](https://github.com/OCA/l10n-ecuador), AGPL-3, pinned in `deploy/Dockerfile` | Build the XML, sign it (XAdES-BES with the company's `.p12`), send it to the SRI web services (test: `celcer.sri.gob.ec`, production: `cel.sri.gob.ec`), store the authorization, email the RIDE. Invoices, credit notes, debit notes and purchase liquidations. |
| `l10n_ec_pos_sri` | `addons/` in this repository | Connects the point of sale: SRI payment method per POS payment method, checks at the till, and a sale that is never lost if the invoice fails. |

Only orders the cashier marks as **Invoice** become electronic invoices, all under one RUC. The POS records every sale; the rest stay receipts that the accountant handles separately. Point of Sale > Orders has the filters **Invoiced**, **Not Invoiced** and **Invoice Failed** for that: filter by date, then export.

Sending to the SRI happens after the invoice is posted, in the `account_edi` scheduled job ("EDI: Perform web services operations"). If the SRI is down, the job retries; the cashier never waits for it.

Not covered by the OCA 19.0 modules: issuing withholdings (`l10n_ec_withhold` only exists for 17.0) and delivery guides. Issue those with the SRI's online invoicer if needed.

## 1. Server: build the new image

The image adds the OCA modules under `/mnt/oca-addons` and the Python libraries they need (`cryptography>=42`, `pyopenssl>=24.1`, `xmlsig`, `xades`, `packaging`). On the server, from `/opt/odoo`, after copying `Dockerfile` and `compose.yaml` from `deploy/` and `addons/l10n_ec_pos_sri` into `/opt/odoo/addons/`:

1. Add `/mnt/oca-addons` to `addons_path` in **both** `config/odoo.conf` and `config-staging/odoo.conf`:
   `addons_path = /mnt/extra-addons,/mnt/oca-addons,/usr/lib/python3/dist-packages/odoo/addons`
   (`setup-staging.sh` already writes it for future staging refreshes.)
2. `docker compose build` then `docker compose up -d odoo odoo-staging`.
3. Check memory for a day with `docker stats --no-stream`.

Production does not change until the modules are installed in `jats` (step 4).

## 2. Staging: install and configure

In `jats_staging` (SSH tunnel, `http://localhost:8070`), Apps, remove the "Apps" filter:

1. Install **Electronic Ecuadorian Localization** (`l10n_ec_account_edi`, pulls `l10n_ec_base`) and **Ecuador POS - SRI electronic invoices** (`l10n_ec_pos_sri`).
2. Company (Settings > Companies): RUC, street, and on the company's contact the fiscal position that matches the taxpayer type (RIMPE, obligated to keep accounts or not).
3. Invoicing > Configuration > Settings > Ecuadorian Localization:
   - Electronic signature: upload the `.p12` and type its password (the owner does this; nobody else handles it).
   - Environment: **Test**.
   - XML versions for invoice and credit note.
   - RUC Provider: confirm with the accountant what goes here for an in-house system (Resolution NAC-DGERCGC26-00000027).
4. Sales journal used by the POS (Invoicing > Configuration > Journals): *Use Documents*, entity `001`, emission point `001` (the numbers the SRI authorized), emission address, SRI payment method.
5. Point of Sale > Configuration > Settings: invoice journal = that journal.
6. Point of Sale > Configuration > Payment Methods: check the **SRI Payment Method** column (Cash `01`, Card `19` or `16`, Transfer `20`).
7. Taxes: each product carries IVA 15 % or 0 % as the accountant decides; each tax needs its *Tax Code for Electronic Documents*.

## 3. Staging: test against the SRI test servers

Each case: make the sale in the POS, open the invoice, wait for (or run) the EDI job, and check the state is *Sent* with an authorization number.

- [ ] Receipt without Invoice: no invoice is created.
- [ ] Invoice to a customer with cédula, paid in cash (`formaPago` 01).
- [ ] Invoice to a customer with RUC, paid by card (`formaPago` 19).
- [ ] Invoice to Consumidor Final under USD 50, and the till refusing one over USD 50.
- [ ] Invoice with a discount, and with IVA 0 % and 15 % products together.
- [ ] Customer without identification number: the till asks for it.
- [ ] Return of an invoiced sale: credit note authorized (not allowed for Consumidor Final).
- [ ] Break the configuration on purpose (remove the certificate): the sale is saved, the POS shows the invoice error, the order says why; restore it and invoice from the order.
- [ ] Point of Sale > Orders: the receipt shows under **Not Invoiced**, the invoice under **Invoiced**, the broken one under **Invoice Failed**.
- [ ] RIDE received by email by a customer with an email address.

## 4. Production

1. Fresh backup of `jats`.
2. Install the same modules in `jats`, repeat the configuration of step 2 with Environment: **Production** and the real emission point.
3. First day: keep the SRI's online invoicer at hand, check every invoice reaches *Sent*, and look at Invoicing > Configuration > Ecuadorian SRI > XML Electronic Documents at closing time.

## Safety

- `setup-staging.sh` forces the SRI **test** environment in `jats_staging` after every refresh, so a staging copy of production can never authorize a real invoice.
- The `.p12` and its password live only in Odoo (uploaded by the owner) and in the encrypted database backups; never in the repository or in logs.
- The OCA module emails RIDEs. Staging cannot send email (see `deploy/README.md`).
