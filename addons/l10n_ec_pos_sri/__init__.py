from . import models


def _l10n_ec_pos_sri_post_init(env):
    """Give existing payment methods their default SRI payment method."""
    methods = env["pos.payment.method"].search([("l10n_ec_sri_payment_id", "=", False)])
    for method in methods:
        method.l10n_ec_sri_payment_id = method._l10n_ec_default_sri_payment()
    # The receipt QR code lets any customer turn a receipt into an invoice
    # from the web; in Ecuador only the cashier decides which sales are
    # invoiced, so stop printing it.
    env["res.company"].search([]).filtered(
        lambda c: c.country_code == "EC" and c.point_of_sale_use_ticket_qr_code
    ).point_of_sale_use_ticket_qr_code = False
