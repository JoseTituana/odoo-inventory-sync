# Prepare a database for SRI electronic invoices from the POS.
#
# Runs inside `odoo shell` and only fills what is empty or obviously wrong.
# It never touches the electronic signature (.p12): the owner uploads it.
#
#   cd /opt/odoo
#   docker compose run --rm --no-deps -T odoo-staging \
#       odoo shell -c /etc/odoo/odoo.conf -d jats_staging --no-http \
#       < sri-configure.py
#
# Optional environment variables (pass with `-e NAME=value` after `run`):
#   SRI_ENTITY    establishment number authorized by the SRI (default 001)
#   SRI_EMISSION  emission point authorized by the SRI (default 001)
#   SRI_DRY_RUN=1 only report, change nothing
#
# Refuses to run on any database other than jats_staging unless
# SRI_ALLOW_DB names it, so production is never changed by accident.

import os

from odoo.exceptions import UserError, ValidationError

ENTITY = os.environ.get("SRI_ENTITY", "001")
EMISSION = os.environ.get("SRI_EMISSION", "001")
DRY_RUN = os.environ.get("SRI_DRY_RUN") == "1"
ALLOWED_DB = os.environ.get("SRI_ALLOW_DB", "jats_staging")

done, todo = [], []


def change(record, values, label):
    """Write only the values that differ, and remember what changed."""
    values = {k: v for k, v in values.items() if record[k] != v and not (
        hasattr(record[k], "id") and record[k].id == v)}
    if not values:
        return
    if not DRY_RUN:
        try:
            with env.cr.savepoint():  # noqa: F821 (env comes from odoo shell)
                record.write(values)
        except (UserError, ValidationError) as error:
            todo.append(f"{label}: could not set {sorted(values)}: {error}")
            return
    done.append(f"{label}: {', '.join(sorted(values))}")


db = env.cr.dbname  # noqa: F821
if db != ALLOWED_DB:
    raise SystemExit(
        f"Refusing to configure '{db}'. This script is for '{ALLOWED_DB}'. "
        "Set SRI_ALLOW_DB to the database name once the owner approves it."
    )

company = env.company  # noqa: F821
if company.country_code != "EC":
    raise SystemExit(f"Company {company.name} is not Ecuadorian; nothing to do.")

# Company: test environment outside production, XML versions.
if db != "jats":
    change(company, {"l10n_ec_type_environment": "test"}, "Company")
for field, version in (
    ("l10n_ec_invoice_version", "1.1.0"),
    ("l10n_ec_credit_note_version", "1.1.0"),
):
    if not company[field]:
        change(company, {field: version}, "Company")
if company.point_of_sale_use_ticket_qr_code:
    # The receipt QR lets customers invoice a receipt themselves.
    change(company, {"point_of_sale_use_ticket_qr_code": False}, "Company (receipt QR for self-invoicing)")
if not company.vat:
    todo.append("Company: RUC is empty (Settings > Companies).")
if not company.partner_id.street:
    todo.append("Company: street is empty; the SRI needs the address.")
if not company.l10n_ec_key_type_id:
    todo.append(
        "Electronic signature: the owner uploads the .p12 in Invoicing > "
        "Configuration > Settings > Ecuadorian Localization."
    )

# POS sales journal: documents, establishment, emission point, address.
cash_payment = env.ref("l10n_ec.P1")  # noqa: F821
pos_configs = env["pos.config"].search([("company_id", "=", company.id)])  # noqa: F821
journal = pos_configs.invoice_journal_id[:1] or env["account.journal"].search(  # noqa: F821
    [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
)
if not journal:
    todo.append("No sales journal found.")
else:
    label = f"Journal {journal.name}"
    change(journal, {"l10n_latam_use_documents": True}, label)
    change(journal, {"l10n_ec_entity": ENTITY, "l10n_ec_emission": EMISSION}, label)
    if not journal.l10n_ec_emission_address_id:
        change(journal, {"l10n_ec_emission_address_id": company.partner_id.id}, label)
    if not journal.l10n_ec_sri_payment_id:
        change(journal, {"l10n_ec_sri_payment_id": cash_payment.id}, label)

    for config in pos_configs:
        label = f"POS {config.name}"
        if config.invoice_journal_id != journal:
            change(config, {"invoice_journal_id": journal.id}, label)
        if config.current_session_id:
            todo.append(f"{label}: a session is open; close it before testing.")

# POS payment methods: SRI payment code.
methods = env["pos.payment.method"].search([("company_id", "=", company.id)])  # noqa: F821
for method in methods.filtered(lambda m: not m.l10n_ec_sri_payment_id):
    change(
        method,
        {"l10n_ec_sri_payment_id": method._l10n_ec_default_sri_payment().id},
        f"Payment method {method.name}",
    )
summary = [
    f"{m.name}: {m.l10n_ec_sri_payment_id.code or '--'} {m.l10n_ec_sri_payment_id.name or ''}"
    for m in methods
]

# Sale taxes used by products: each needs its code for the XML.
sale_taxes = env["account.tax"].search(  # noqa: F821
    [("type_tax_use", "=", "sale"), ("company_id", "=", company.id),
     ("tax_group_id.l10n_ec_type", "in", ("vat05", "vat08", "vat12", "vat13",
                                          "vat14", "vat15", "zero_vat",
                                          "not_charged_vat", "exempt_vat"))]
)
used = env["product.template"].search(  # noqa: F821
    [("company_id", "in", (False, company.id))]
).taxes_id & sale_taxes
for tax in used.filtered(lambda t: not t.l10n_ec_xml_fe_code):
    todo.append(f"Tax {tax.name}: no Tax Code for Electronic Documents.")

if not DRY_RUN:
    env.cr.commit()  # noqa: F821

print(f"\n=== SRI configuration on {db} ({'dry run' if DRY_RUN else 'applied'}) ===")
print(f"Environment: {company.l10n_ec_type_environment}; "
      f"emission point {ENTITY}-{EMISSION}")
print("Changed:" if done else "Changed: nothing")
for line in done:
    print(f"  {line}")
print("SRI code per POS payment method (cards: 19 credit, 16 debit):")
for line in summary:
    print(f"  {line}")
print("Still to do by hand:" if todo else "Still to do by hand: nothing")
for line in todo:
    print(f"  - {line}")
