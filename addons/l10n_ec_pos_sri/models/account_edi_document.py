from odoo import models


class AccountEdiDocument(models.Model):
    _inherit = "account.edi.document"

    def _l10n_ec_compute_amount_discount(self):
        """totalDescuento without IVA: the sum of the lines' descuento."""
        self.ensure_one()
        return sum(
            line._prepare_edi_vals_to_export()["price_discount"]
            for line in self.move_id.invoice_line_ids.filtered(lambda x: x.display_type == "product")
        )
