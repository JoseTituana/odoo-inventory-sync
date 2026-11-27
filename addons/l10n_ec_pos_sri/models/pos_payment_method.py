from odoo import api, fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    l10n_ec_sri_payment_id = fields.Many2one(
        "l10n_ec.sri.payment",
        string="SRI Payment Method",
        compute="_compute_l10n_ec_sri_payment_id",
        store=True,
        readonly=False,
        help="Ecuador: payment method (formaPago) written on the electronic "
             "invoice when this method pays most of the order. Cash: 01 No use "
             "of the financial system; cards: 16 or 19; transfers: 20.",
    )

    def _l10n_ec_default_sri_payment(self):
        """01 for cash and customer account, 20 for anything through a bank."""
        self.ensure_one()
        if self.is_cash_count or self.type == "pay_later":
            xmlid = "l10n_ec.P1"
        else:
            xmlid = "l10n_ec.P20"
        return self.env.ref(xmlid, raise_if_not_found=False) or self.env["l10n_ec.sri.payment"]

    @api.depends("type", "is_cash_count")
    def _compute_l10n_ec_sri_payment_id(self):
        for method in self:
            if not method.l10n_ec_sri_payment_id:
                method.l10n_ec_sri_payment_id = method._l10n_ec_default_sri_payment()
