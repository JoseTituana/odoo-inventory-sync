from odoo import api, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _l10n_ec_final_consumer(self):
        return self.env.ref("l10n_ec.ec_final_consumer", raise_if_not_found=False)

    def get_limited_partners_loading(self, offset=0):
        """Always load Consumidor Final so the cashier can pick it offline."""
        partner_ids = super().get_limited_partners_loading(offset)
        final_consumer = self._l10n_ec_final_consumer()
        if final_consumer and self.company_id.country_code == "EC" \
                and (final_consumer.id,) not in partner_ids:
            partner_ids.append((final_consumer.id,))
        return partner_ids

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if read_records and config.company_id.country_code == "EC":
            final_consumer = config._l10n_ec_final_consumer()
            limit = self.env["ir.config_parameter"].sudo().get_param(
                "l10n_ec_final_consumer_limit", 50)
            read_records[0]["_l10n_ec_final_consumer_id"] = final_consumer.id if final_consumer else False
            read_records[0]["_l10n_ec_final_consumer_limit"] = float(limit)
        return read_records
