from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pos.config"].create({"name": "Guard test shop"})
        cls.location = cls.config.picking_type_id.default_location_src_id
        cls.shoe = cls.env["product.product"].create({
            "name": "Guard test shoe", "type": "consu", "is_storable": True,
            "available_in_pos": True})
        cls.service = cls.env["product.product"].create({
            "name": "Guard test service", "type": "service", "available_in_pos": True})
        cls.env["stock.quant"]._update_available_quantity(cls.shoe, cls.location, 3)
        cls.manager = cls.env["hr.employee"].create({"name": "Guard manager", "pin": "4321"})
        cls.cashier = cls.env["hr.employee"].create({"name": "Guard cashier", "pin": "1111"})
        cls.config.write({"advanced_employee_ids": [(6, 0, cls.manager.ids)],
                          "basic_employee_ids": [(6, 0, cls.cashier.ids)]})

    def available(self, products):
        return self.env["pos.config"].stock_guard_available(self.config.id, products.ids)

    def authorize(self, pin):
        # English messages whatever the database language (staging is es_EC).
        return self.env["pos.config"].with_context(lang="en_US").stock_guard_authorize(
            self.config.id, pin, self.shoe.id, 4, 3, "Guard cashier")

    def logs(self):
        return self.env["pos.stock.override"].search([("config_id", "=", self.config.id)])

    def test_reports_on_hand_stock_at_the_pos_location(self):
        self.assertEqual(self.available(self.shoe), {str(self.shoe.id): 3.0})

    def test_products_without_inventory_are_not_checked(self):
        self.assertEqual(self.available(self.service), {})

    def test_manager_pin_authorizes_and_is_logged(self):
        result = self.authorize("4321")
        self.assertEqual(result, {"ok": True, "employee": "Guard manager"})
        log = self.logs()
        self.assertEqual(len(log), 1)
        self.assertTrue(log.success)
        self.assertEqual(log.employee_id, self.manager)
        self.assertEqual((log.qty_requested, log.qty_available), (4, 3))

    def test_cashier_pin_and_wrong_pin_are_refused_and_logged(self):
        for pin in ("1111", "0000", ""):
            self.assertFalse(self.authorize(pin)["ok"])
        self.assertEqual(len(self.logs()), 3)
        self.assertFalse(any(self.logs().mapped("success")))

    def test_too_many_wrong_pins_lock_even_the_right_one(self):
        for _ in range(5):
            self.authorize("9999")
        result = self.authorize("4321")
        self.assertFalse(result["ok"])
        self.assertIn("Too many", result["error"])
        self.assertEqual(len(self.logs()), 5)  # the locked attempt is not logged again

    def test_no_manager_with_pin_gives_a_clear_error(self):
        # POS administrators of the database also count as managers; clear
        # every PIN (rolled back after the test) so real data does not matter.
        self.config._stock_guard_managers().write({"pin": False})
        result = self.authorize("4321")
        self.assertFalse(result["ok"])
        self.assertIn("No manager", result["error"])

    def test_snapshot_lists_every_storable_pos_product(self):
        snapshot = self.env["pos.config"].stock_guard_snapshot(self.config.id)
        self.assertEqual(snapshot[str(self.shoe.id)], 3.0)
        self.assertNotIn(str(self.service.id), snapshot)

    def test_offline_sales_are_logged_once(self):
        entries = [{"ref": "order-1:%s" % self.shoe.id, "product_id": self.shoe.id,
                    "qty_requested": 2, "qty_available": 3, "cashier": "Guard cashier"}]
        config = self.env["pos.config"]
        self.assertEqual(config.stock_guard_log_offline(self.config.id, entries), 1)
        self.assertEqual(config.stock_guard_log_offline(self.config.id, entries), 0)  # resent
        log = self.logs()
        self.assertEqual(len(log), 1)
        self.assertTrue(log.offline)
        self.assertFalse(log.success)
        self.assertEqual((log.product_id, log.qty_requested), (self.shoe, 2))

    def test_offline_sales_do_not_count_as_wrong_pins(self):
        entries = [{"ref": "order-%s" % i, "product_id": self.shoe.id, "qty_requested": 1,
                    "qty_available": 3} for i in range(6)]
        self.env["pos.config"].stock_guard_log_offline(self.config.id, entries)
        self.assertTrue(self.authorize("4321")["ok"])
