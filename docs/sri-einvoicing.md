# Electronic invoices with the SRI (Ecuador)

How the point of sale issues Ecuadorian electronic invoices without a paid provider, and how to roll it out: staging first, against the SRI test servers, then production.

Owner's step-by-step guide in Spanish: [manual/guia-activar-facturacion-sri.html](manual/guia-activar-facturacion-sri.html) (printed as `Guia-activar-facturacion-SRI.pdf`).

## How it fits together

| Piece | Where | What it does |
|---|---|---|
| `l10n_ec` | Odoo Community | Ecuadorian chart of accounts, taxes, document types, identification types. Already installed in `jats`. |
| `l10n_ec_base`, `l10n_ec_account_edi` | [OCA l10n-ecuador](https://github.com/OCA/l10n-ecuador), AGPL-3, pinned in `deploy/Dockerfile` | Build the XML, sign it (XAdES-BES with the company's `.p12`), send it to the SRI web services (test: `celcer.sri.gob.ec`, production: `cel.sri.gob.ec`), store the authorization, email the RIDE. Invoices, credit notes, debit notes and purchase liquidations. |
| `l10n_ec_pos_sri` | `addons/` in this repository | Connects the point of sale: SRI payment method per POS payment method, checks at the till, and a sale that is never lost if the invoice fails. |

Only orders the cashier marks as **Invoice** become electronic invoices, all under one RUC. The POS records every sale; the rest stay receipts that the accountant handles separately. Point of Sale > Orders has the filters **Invoiced**, **Not Invoiced** and **Invoice Failed** for that: filter by date, then export.

Sending to the SRI happens after the invoice is posted, in the `account_edi` scheduled job ("EDI: Perform web services operations"). If the SRI is down, the job retries; the cashier never waits for it.

Other rules `l10n_ec_pos_sri` enforces:

- Receipts do not print the "Need an invoice?" QR code, so customers cannot invoice a receipt themselves from the web.
- A return of an invoiced sale, marked as Invoice, becomes a credit note of that invoice. A return of a receipt, or of an invoice to Consumidor Final, cannot become a credit note: the return is saved and the order says why.
- Invoice lines are sent without IVA and after the discount, so they add up for the SRI when shelf prices include IVA (the OCA 19.0.1.0.0.2 module sends the IVA-included price and the amount before the discount).

Not covered by the OCA 19.0 modules: issuing withholdings (`l10n_ec_withhold` only exists for 17.0) and delivery guides. Issue those with the SRI's online invoicer if needed.

## 1. Server: add invoicing with one command

From a store PC where `ssh vps` works, in PowerShell, in a copy of this repository:

```
.\deploy\enable-sri.ps1 -Target staging      # first: jats_staging
.\deploy\enable-sri.ps1 -Target production   # after the tests pass: jats
```

It copies `Dockerfile`, `compose.yaml`, `enable-sri.sh`, `sri-configure.py` and `addons/l10n_ec_pos_sri` to `/opt/odoo` and runs `enable-sri.sh`, which adds `/mnt/oca-addons` to `addons_path` (copy of the old file as `odoo.conf.before-sri`), builds `odoo-josheps:19`, makes a backup (production only), installs or updates `l10n_ec_pos_sri`, runs `sri-configure.py`, and starts the service again with `up -d --no-deps`. It can run again safely. On a failed install it starts the service as it was and leaves the database unchanged.

Nothing changes on the store PCs: the till picks up the new version when the "Caja Josheps" shortcut is closed and opened again.

The image needs a few hundred MB of disk and `docker compose build` downloads the Python libraries; check memory afterwards with `docker stats --no-stream`.

## 2. Staging: configure (by hand, or what the script already did)

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

### Shortcut: `sri-configure.py`

`deploy/sri-configure.py` does steps 3 (environment and XML versions), 4, 5 and 6 below in one go, and lists what is still missing (RUC, street, the `.p12`, tax codes). It only fills empty values, can run again safely, and refuses any database other than `jats_staging`. Copy it to `/opt/odoo` and run:

```
docker compose run --rm --no-deps -T odoo-staging odoo shell -c /etc/odoo/odoo.conf -d jats_staging --no-http < sri-configure.py
```

Add `-e SRI_DRY_RUN=1` after `run` to only see the report, and `-e SRI_ENTITY=002 -e SRI_EMISSION=003` if the SRI authorized other numbers than 001-001. Card payment methods get code 20; change them to 19 (credit) or 16 (debit) by hand.

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

1. After closing the till: `.\deploy\enable-sri.ps1 -Target production` (it makes the backup first).
2. Repeat the configuration of step 2 with Environment: **Production** and the real emission point.
3. First day: keep the SRI's online invoicer at hand, check every invoice reaches *Sent*, and look at Invoicing > Configuration > Ecuadorian SRI > XML Electronic Documents at closing time.

## Safety

- `setup-staging.sh` forces the SRI **test** environment in `jats_staging` after every refresh, so a staging copy of production can never authorize a real invoice.
- The `.p12` and its password live only in Odoo (uploaded by the owner) and in the encrypted database backups; never in the repository or in logs.
- The OCA module emails RIDEs. Staging cannot send email (see `deploy/README.md`).
