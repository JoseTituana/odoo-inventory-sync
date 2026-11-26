from odoo import fields, models


class PosStockOverride(models.Model):
    """Log of out-of-stock sale authorizations and failed PIN attempts."""

    _name = "pos.stock.override"
    _description = "POS out-of-stock authorization"
    _order = "create_date desc, id desc"

    config_id = fields.Many2one("pos.config", string="Point of Sale", required=True,
                                ondelete="cascade", index=True)
    session_id = fields.Many2one("pos.session", string="Session", ondelete="set null")
    product_id = fields.Many2one("product.product", string="Product", ondelete="set null")
    employee_id = fields.Many2one("hr.employee", string="Authorized by", ondelete="set null")
    cashier = fields.Char(string="Cashier")
    qty_requested = fields.Float(string="Quantity on order")
    qty_available = fields.Float(string="Quantity in stock")
    success = fields.Boolean(string="Authorized", index=True)
    offline = fields.Boolean(
        string="Sold offline", index=True,
        help="Sold while the point of sale had no connection: checked only against the last "
             "stock the till had downloaded, without a manager PIN. Review and recount.")
    offline_ref = fields.Char(string="Offline reference", index=True, copy=False)
