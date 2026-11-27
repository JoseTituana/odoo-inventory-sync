import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { formatCurrency } from "@web/core/currency";

// Ecuador: an order the cashier marks as Invoice is checked before it is
// saved, so the SRI rules are explained at the till instead of failing later.
patch(OrderPaymentValidation.prototype, {
    async isOrderValid(isForceValidate) {
        const valid = await super.isOrderValid(...arguments);
        if (!valid || !this.order.isToInvoice() || this.pos.company.country_id?.code !== "EC") {
            return valid;
        }
        const partner = this.order.getPartner();
        if (!partner) {
            return valid; // the standard check already asked for a customer
        }
        if (!partner.vat) {
            this.pos.dialog.add(AlertDialog, {
                title: _t("Missing identification"),
                body: _t(
                    "An electronic invoice needs the customer's cédula, RUC or passport number."
                ),
                confirmLabel: _t("Add it"),
                confirm: () => this.pos.editPartner(partner),
            });
            return false;
        }
        const finalConsumerId = this.pos.config._l10n_ec_final_consumer_id;
        const limit = this.pos.config._l10n_ec_final_consumer_limit;
        if (
            finalConsumerId &&
            partner.id === finalConsumerId &&
            limit &&
            Math.abs(this.order.priceIncl) > limit
        ) {
            this.pos.dialog.add(AlertDialog, {
                title: _t("Customer required"),
                body: _t(
                    "Invoices to Consumidor Final cannot exceed %s. Ask for the customer's cédula or RUC.",
                    formatCurrency(limit, this.pos.currency.id)
                ),
            });
            return false;
        }
        return valid;
    },
});
