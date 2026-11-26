import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";

// Blocks out-of-stock sales unless a manager authorizes them with a PIN.
// Checked when a product is added and again when the cashier presses Payment,
// which also catches quantities typed on the numpad.
patch(PosStore.prototype, {
    async addLineToOrder(vals, order, opts = {}, configure = true) {
        const previous = new Map(order.lines.map((line) => [line, line.getQuantity()]));
        const line = await super.addLineToOrder(...arguments);
        if (!line) {
            return line;
        }
        if (!(await this.stockGuardEnsure(order, [line.getProduct()]))) {
            if (previous.has(line)) {
                line.setQuantity(previous.get(line)); // undo the merged +1
            } else {
                order.removeOrderline(line);
            }
            return false;
        }
        return line;
    },

    async pay() {
        const order = this.getOrder();
        if (order && !(await this.stockGuardEnsure(order, null))) {
            return;
        }
        return await super.pay(...arguments);
    },

    stockGuardNeeded(order) {
        const needed = new Map();
        for (const line of order.lines) {
            const qty = line.getQuantity();
            if (qty <= 0) {
                continue; // refunds and returns put stock back
            }
            const product = line.getProduct();
            const entry = needed.get(product.id) || { product, qty: 0 };
            entry.qty += qty;
            needed.set(product.id, entry);
        }
        return needed;
    },

    async stockGuardEnsure(order, products) {
        const needed = this.stockGuardNeeded(order);
        const ids = (products ? products.map((p) => p.id) : [...needed.keys()]).filter((id) =>
            needed.has(id)
        );
        if (!ids.length) {
            return true;
        }
        let available;
        try {
            available = await this.data.call("pos.config", "stock_guard_available", [
                this.config.id,
                ids,
            ]);
        } catch {
            this.stockGuardOffline();
            return false;
        }
        this.stockGuardApproved = this.stockGuardApproved || {};
        for (const id of ids) {
            const stock = available[String(id)];
            if (stock === undefined) {
                continue; // the product does not track inventory
            }
            const { product, qty } = needed.get(id);
            const key = `${order.uuid}:${id}`;
            if (qty <= stock || (this.stockGuardApproved[key] ?? -1) >= qty) {
                continue;
            }
            if (!(await this.stockGuardAuthorize(product, qty, stock))) {
                return false;
            }
            this.stockGuardApproved[key] = qty;
        }
        return true;
    },

    async stockGuardAuthorize(product, qty, stock) {
        const name = product.display_name || product.name;
        const confirmed = await ask(this.dialog, {
            title: _t("Not enough stock"),
            body: _t(
                "%(product)s: the system has %(stock)s in stock and this order needs %(qty)s. A manager PIN is required to sell it.",
                { product: name, stock: Math.max(stock, 0), qty }
            ),
            confirmLabel: _t("Enter manager PIN"),
            cancelLabel: _t("Cancel"),
        });
        if (!confirmed) {
            return false;
        }
        const pin = await makeAwaitable(this.dialog, NumberPopup, {
            title: _t("Manager PIN"),
            formatDisplayedValue: (x) => x.replace(/./g, "•"),
        });
        if (!pin) {
            return false;
        }
        let result;
        try {
            result = await this.data.call("pos.config", "stock_guard_authorize", [
                this.config.id,
                pin,
                product.id,
                qty,
                stock,
                this.getCashier()?.name || "",
            ]);
        } catch {
            this.stockGuardOffline();
            return false;
        }
        if (!result.ok) {
            this.dialog.add(AlertDialog, { title: _t("Not authorized"), body: result.error });
            return false;
        }
        this.notification.add(_t("Authorized by %s", result.employee), { type: "success" });
        return true;
    },

    stockGuardOffline() {
        this.dialog.add(AlertDialog, {
            title: _t("Cannot check stock"),
            body: _t(
                "The point of sale is offline, so stock and manager PINs cannot be checked. This product cannot be sold until the connection is back."
            ),
        });
    },
});
