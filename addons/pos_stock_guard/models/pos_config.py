import hmac
from datetime import timedelta

from odoo import _, api, fields, models

MAX_FAILED_ATTEMPTS = 5
FAILED_WINDOW = timedelta(minutes=10)


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _stock_guard_location(self):
        self.ensure_one()
        return self.picking_type_id.default_location_src_id

    def _stock_guard_pending(self, products):
        """Quantities sold in still-open sessions whose stock moves do not exist yet.

        With "update stock at session closing", paid orders only create their
        pickings when the session closes, so on-hand stock alone would let the
        same pair be sold twice.
        """
        self.ensure_one()
        lines = self.env["pos.order.line"].sudo().search([
            ("order_id.config_id", "=", self.id),
            ("order_id.state", "in", ["paid", "done"]),
            ("order_id.picking_ids", "=", False),
            ("order_id.session_id.state", "!=", "closed"),
            ("product_id", "in", products.ids),
        ])
        pending = {}
        for line in lines:
            pending[line.product_id.id] = pending.get(line.product_id.id, 0.0) + line.qty
        return pending

    def _stock_guard_managers(self):
        """Employees allowed to authorize: this POS's managers and POS admins, with a PIN."""
        self.ensure_one()
        employees = self.advanced_employee_ids
        admin_group = self.group_pos_manager_id
        if admin_group:
            employees |= self.env["hr.employee"].sudo().search([
                ("user_id.all_group_ids", "in", admin_group.ids),
                ("company_id", "in", [False, self.company_id.id]),
            ])
        return employees.sudo().filtered("pin")

    @api.model
    def stock_guard_available(self, config_id, product_ids):
        """Sellable quantity per storable product id (string keys for JSON).

        Products that do not track inventory are left out: they are never blocked.
        """
        config = self.browse(config_id).exists()
        config.check_access("read")
        location = config._stock_guard_location()
        products = self.env["product.product"].browse(product_ids).exists().filtered("is_storable")
        if not location or not products:
            return {}
        pending = config._stock_guard_pending(products)
        result = {}
        for product in products.with_context(location=location.id):
            result[str(product.id)] = product.qty_available - pending.get(product.id, 0.0)
        return result

    @api.model
    def stock_guard_snapshot(self, config_id):
        """Sellable quantity of every storable product of the point of sale.

        The till keeps it to check sales while it is offline.
        """
        config = self.browse(config_id).exists()
        config.check_access("read")
        products = self.env["product.product"].search([
            ("available_in_pos", "=", True), ("is_storable", "=", True),
            ("company_id", "in", [False, config.company_id.id])])
        return self.stock_guard_available(config.id, products.ids)

    @api.model
    def stock_guard_log_offline(self, config_id, entries):
        """Record sales the till let through while offline, once it is back online.

        ``entries``: dicts with ref, product_id, qty_requested, qty_available and
        cashier. A ref already logged is skipped, so the till can safely resend.
        """
        config = self.browse(config_id).exists()
        config.check_access("read")
        log = self.env["pos.stock.override"].sudo()
        refs = [str(e.get("ref") or "")[:128] for e in entries]
        done = set(log.search([("config_id", "=", config.id),
                               ("offline_ref", "in", [r for r in refs if r])]).mapped("offline_ref"))
        product_ids = set(self.env["product.product"].browse(
            [int(e.get("product_id") or 0) for e in entries]).exists().ids)
        values = []
        for ref, entry in zip(refs, entries):
            if not ref or ref in done:
                continue
            done.add(ref)
            product_id = int(entry.get("product_id") or 0)
            values.append({
                "config_id": config.id,
                "session_id": config.current_session_id.id or False,
                "product_id": product_id if product_id in product_ids else False,
                "cashier": str(entry.get("cashier") or "")[:128],
                "qty_requested": float(entry.get("qty_requested") or 0),
                "qty_available": float(entry.get("qty_available") or 0),
                "success": False,
                "offline": True,
                "offline_ref": ref,
            })
        log.create(values)
        return len(values)

    @api.model
    def stock_guard_authorize(self, config_id, pin, product_id, qty_requested, qty_available,
                              cashier=""):
        """Check a manager PIN on the server (never in the browser) and log the attempt."""
        config = self.browse(config_id).exists()
        config.check_access("read")
        log = self.env["pos.stock.override"].sudo()
        since = fields.Datetime.now() - FAILED_WINDOW
        failures = log.search_count([("config_id", "=", config.id), ("success", "=", False),
                                     ("offline", "=", False), ("create_date", ">=", since)])
        if failures >= MAX_FAILED_ATTEMPTS:
            return {"ok": False,
                    "error": _("Too many wrong PINs. Wait 10 minutes and try again.")}

        managers = config._stock_guard_managers()
        if not managers:
            return {"ok": False, "error": _(
                "No manager with a PIN is set up for this point of sale. Add one in "
                "Point of Sale > Configuration > Settings > Employees.")}
        pin = str(pin or "")
        employee = next((e for e in managers if hmac.compare_digest(e.pin, pin)), None)
        log.create({
            "config_id": config.id,
            "session_id": config.current_session_id.id or False,
            "product_id": product_id or False,
            "employee_id": employee.id if employee else False,
            "cashier": (cashier or "")[:128],
            "qty_requested": qty_requested,
            "qty_available": qty_available,
            "success": bool(employee),
        })
        if not employee:
            return {"ok": False, "error": _("Wrong PIN, or this employee is not a manager.")}
        return {"ok": True, "employee": employee.name}
