{
    "name": "Ecuador POS - SRI electronic invoices",
    "summary": "Point of sale invoices go to the SRI through the OCA module "
               "l10n_ec_account_edi, with the SRI payment method, the final "
               "consumer limit and a sale that is never blocked by the SRI",
    "description": """
Only orders the cashier marks as Invoice become electronic invoices; the
rest stay normal receipts.

* Each point of sale payment method has an SRI payment method (formaPago).
* Before validating an order to invoice, the customer needs an
  identification number, and Consumidor Final cannot exceed the limit the
  OCA module enforces (parameter l10n_ec_final_consumer_limit, 50 by default).
* If Odoo cannot create or post the invoice, the sale is still saved: the
  error is written on the order, the POS shows its usual "invoice error"
  message, and the order stays "to invoice" until someone invoices it from
  the back office.
* Sending to the SRI is done afterwards by the account_edi scheduled job, so a
  slow or unavailable SRI never stops the cashier.
""",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations/Point of Sale",
    "license": "AGPL-3",
    "depends": ["point_of_sale", "l10n_ec_account_edi"],
    "data": [
        "views/pos_payment_method_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "l10n_ec_pos_sri/static/src/**/*",
        ],
    },
    "post_init_hook": "_l10n_ec_pos_sri_post_init",
    "installable": True,
}
