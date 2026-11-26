from . import models


def _l10n_ec_pos_sri_post_init(env):
    """Give existing payment methods their default SRI payment method."""
    methods = env["pos.payment.method"].search([("l10n_ec_sri_payment_id", "=", False)])
    for method in methods:
        method.l10n_ec_sri_payment_id = method._l10n_ec_default_sri_payment()
