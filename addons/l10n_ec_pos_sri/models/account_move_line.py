from odoo import models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    # The SRI wants, per line: precioTotalSinImpuesto = cantidad x precioUnitario
    # - descuento, all without IVA, and equal to the line's baseImponible.
    # l10n_ec_account_edi 19.0.1.0.0.2 sends the shelf price (with IVA when
    # prices include it) and the subtotal before the discount, so a store with
    # IVA-included prices or a discount would send lines that do not add up.

    def _l10n_ec_sri_line_amounts(self, values):
        edi_values = self._prepare_edi_vals_to_export()
        EdiDocument = self.env["account.edi.document"]
        gross = edi_values["price_subtotal_before_discount"]
        unit = gross / self.quantity if self.quantity else 0.0
        values.update({
            "precioUnitario": EdiDocument._l10n_ec_number_format(unit, decimals=6),
            "descuento": EdiDocument._l10n_ec_number_format(
                edi_values["price_discount"], decimals=6),
            "precioTotalSinImpuesto": EdiDocument._l10n_ec_number_format(
                self.price_subtotal, decimals=6),
        })
        return values

    def l10n_ec_get_invoice_edi_data(self, taxes_data):
        return self._l10n_ec_sri_line_amounts(super().l10n_ec_get_invoice_edi_data(taxes_data))

    def l10n_ec_get_credit_note_edi_data(self, taxes_data):
        return self._l10n_ec_sri_line_amounts(super().l10n_ec_get_credit_note_edi_data(taxes_data))
