import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ConnectionLostError } from "@web/core/network/rpc";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";

// Blocks out-of-stock sales unless a manager authorizes them with a PIN.
// Checked when a product is added and again when the cashier presses Payment,
// which also catches quantities typed on the numpad.
//
// Offline, the server cannot be asked, so the till uses the last stock it
// downloaded (kept in the browser) minus the paid orders it has not sent yet.
// Sales within that stock go through and are logged as "sold offline" once
// the connection is back; sales beyond it stay blocked, because a manager PIN
// can only be checked on the server.
patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        this.stockGuardRefresh();
        this.stockGuardFlushOffline();
        return result;
    },

    async posBackOnline() {
        const result = await super.posBackOnline(...arguments);
        this.stockGuardFlushOffline();
        this.stockGuardRefresh();
        return result;
    },

    postSyncAllOrders(orders) {
        const result = super.postSyncAllOrders(...arguments);
        // The server has now moved this stock; keep the offline copy in step.
        // Draft orders are synced too, and an order can be synced more than
        // once: count each paid order a single time.
        const cache = this.stockGuardCache();
        const counted = this.stockGuardLoad("counted");
        for (const order of orders || []) {
            if (!["paid", "done", "invoiced"].includes(order.state) || counted[order.uuid]) {
                continue;
            }
            counted[order.uuid] = Date.now();
            for (const line of order.lines || []) {
                const id = String(line.getProduct().id);
                if (cache[id] !== undefined) {
                    cache[id] -= line.getQuantity();
                }
            }
        }
        // Forget orders counted more than two days ago; they will not sync again.
        const old = Date.now() - 2 * 24 * 3600 * 1000;
        for (const [uuid, at] of Object.entries(counted)) {
            if (at < old) {
                delete counted[uuid];
            }
        }
        this.stockGuardStore("counted", counted);
        this.stockGuardSaveCache();
        return result;
    },

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

    // ----------------------------------------------------------- browser copy

    stockGuardKey(name) {
        return `pos_stock_guard.${name}.${this.config.id}`;
    },

    stockGuardLoad(name) {
        try {
            return JSON.parse(localStorage.getItem(this.stockGuardKey(name)) || "{}");
        } catch {
            return {};
        }
    },

    stockGuardStore(name, value) {
        try {
            localStorage.setItem(this.stockGuardKey(name), JSON.stringify(value));
        } catch {
            // Storage full or blocked: the in-memory copy still works.
        }
    },

    stockGuardCache() {
        if (!this._stockGuardCache) {
            this._stockGuardCache = this.stockGuardLoad("stock");
        }
        return this._stockGuardCache;
    },

    stockGuardSaveCache() {
        this.stockGuardStore("stock", this.stockGuardCache());
    },

    async stockGuardRefresh() {
        try {
            const snapshot = await this.data.call("pos.config", "stock_guard_snapshot", [
                this.config.id,
            ]);
            this._stockGuardCache = snapshot || {};
            this.stockGuardSaveCache();
        } catch {
            // Offline at start-up: keep the copy saved last time.
        }
    },

    async stockGuardFlushOffline() {
        const pending = this.stockGuardLoad("offline");
        const entries = Object.values(pending);
        if (!entries.length) {
            return;
        }
        try {
            await this.data.call("pos.config", "stock_guard_log_offline", [
                this.config.id,
                entries,
            ]);
            this.stockGuardStore("offline", {});
        } catch {
            // Still offline: try again next time.
        }
    },

    stockGuardRememberOffline(order, product, qty, stock) {
        const pending = this.stockGuardLoad("offline");
        const ref = `${order.uuid}:${product.id}`;
        pending[ref] = {
            ref,
            product_id: product.id,
            qty_requested: qty,
            qty_available: stock,
            cashier: this.getCashier()?.name || "",
        };
        this.stockGuardStore("offline", pending);
    },

    // Paid orders still waiting to be sent: the server stock does not include them.
    stockGuardUnsent(productId) {
        let qty = 0;
        for (const order of this.models["pos.order"].filter((o) => o.isUnsyncedPaid)) {
            for (const line of order.lines) {
                if (line.getProduct().id === productId) {
                    qty += line.getQuantity();
                }
            }
        }
        return qty;
    },

    // ------------------------------------------------------------------ check

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
        } catch (error) {
            if (error instanceof ConnectionLostError || this.data.network?.offline) {
                return this.stockGuardEnsureOffline(order, ids, needed, !products);
            }
            this.stockGuardError(error);
            return false;
        }
        const cache = this.stockGuardCache();
        Object.assign(cache, available);
        this.stockGuardSaveCache();
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

    stockGuardEnsureOffline(order, ids, needed, atPayment) {
        const cache = this.stockGuardCache();
        this.stockGuardApproved = this.stockGuardApproved || {};
        const toLog = [];
        for (const id of ids) {
            const known = cache[String(id)];
            if (known === undefined) {
                continue; // no inventory tracking, or never downloaded
            }
            const { product, qty } = needed.get(id);
            const stock = known - this.stockGuardUnsent(id);
            const key = `${order.uuid}:${id}`;
            if (qty > stock && (this.stockGuardApproved[key] ?? -1) < qty) {
                this.stockGuardOffline(product, qty, stock);
                return false;
            }
            toLog.push({ product, qty, stock });
        }
        if (atPayment) {
            for (const { product, qty, stock } of toLog) {
                this.stockGuardRememberOffline(order, product, qty, stock);
            }
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
        } catch (error) {
            this.stockGuardError(error);
            return false;
        }
        if (!result.ok) {
            this.dialog.add(AlertDialog, { title: _t("Not authorized"), body: result.error });
            return false;
        }
        this.notification.add(_t("Authorized by %s", result.employee), { type: "success" });
        return true;
    },

    stockGuardOffline(product, qty, stock) {
        const name = product.display_name || product.name;
        this.dialog.add(AlertDialog, {
            title: _t("Not enough stock (offline)"),
            body: _t(
                "%(product)s: the last stock this till downloaded is %(stock)s and this order needs %(qty)s. Without a connection a manager PIN cannot be checked, so this cannot be sold until the connection is back.",
                { product: name, stock: Math.max(stock, 0), qty }
            ),
        });
    },

    stockGuardError(error) {
        const offline = error instanceof ConnectionLostError || this.data.network?.offline;
        this.dialog.add(AlertDialog, {
            title: _t("Cannot check stock"),
            body: offline
                ? _t(
                      "The point of sale is offline, so the manager PIN cannot be checked. Try again when the connection is back."
                  )
                : _t("The server could not check the stock: %s", error?.data?.message || error),
        });
    },
});
