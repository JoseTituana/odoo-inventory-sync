import logging

from markupsafe import Markup

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        if self.company_id.country_code == "EC":
            payments = self.payment_ids.filtered(lambda p: p.amount > 0)
            main = max(payments, key=lambda p: p.amount, default=None)
            sri_payment = main and main.payment_method_id.l10n_ec_sri_payment_id
            if sri_payment:
                vals["l10n_ec_sri_payment_id"] = sri_payment.id
        return vals

    def _process_saved_order(self, draft):
        # Only invoices created while the POS saves a sale are made "safe":
        # invoicing again from the back office still shows every error.
        return super(PosOrder, self.with_context(l10n_ec_pos_safe_invoice=True))._process_saved_order(draft)

    def _generate_pos_order_invoice(self):
        if not self.env.context.get("l10n_ec_pos_safe_invoice") \
                or any(order.company_id.country_code != "EC" for order in self):
            return super()._generate_pos_order_invoice()
        try:
            with self.env.cr.savepoint():
                return super()._generate_pos_order_invoice()
        except UserError as error:
            # The savepoint undid the invoice; the paid sale itself is kept.
            self.env.invalidate_all()
            self._l10n_ec_report_invoice_error(error)
            return self.env["account.move"]

    def _l10n_ec_report_invoice_error(self, error):
        message = str(error.args[0]) if error.args else str(error)
        _logger.warning("POS orders %s saved without invoice: %s", self.mapped("name"), message)
        for order in self:
            order.message_post(body=Markup("<p>%s</p><p>%s</p>") % (
                self.env._("The sale was saved but the electronic invoice could not be "
                           "created. Fix the problem and use the Invoice button on this order."),
                message,
            ))
