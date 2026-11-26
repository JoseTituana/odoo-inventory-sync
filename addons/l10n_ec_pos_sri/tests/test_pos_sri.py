from uuid import uuid4

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.l10n_ec_account_edi.tests.test_edi_common import TestL10nECEdiCommon


@tagged("post_install_l10n", "post_install", "-at_install")
class TestPosSri(TestL10nECEdiCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("point_of_sale.group_pos_manager")
        cls.cash_method = cls.env["pos.payment.method"].create({
            "name": "Cash EC",
            "journal_id": cls.journal_cash.id,
            "company_id": cls.company.id,
        })
        cls.bank_method = cls.env["pos.payment.method"].create({
            "name": "Card EC",
            "journal_id": cls.company_data["default_journal_bank"].id,
            "company_id": cls.company.id,
        })
        cls.credit_card_method = cls.env["pos.payment.method"].create({
            "name": "Credit card EC",
            "journal_id": cls.company_data["default_journal_bank"].id,
            "company_id": cls.company.id,
            "l10n_ec_sri_payment_id": cls.env.ref("l10n_ec.P19").id,
        })
        cls.pos_config = cls.env["pos.config"].create({
            "name": "Local EC",
            "company_id": cls.company.id,
            "invoice_journal_id": cls.journal_sale.id,
            "payment_method_ids": [(6, 0, (cls.cash_method | cls.bank_method | cls.credit_card_method).ids)],
        })
        cls.shoe = cls.env["product.product"].create({
            "name": "Valentina 36",
            "is_storable": True,
            "available_in_pos": True,
            "list_price": 40.0,
            "taxes_id": [(6, 0, cls.tax_sale_a.ids)],
        })

    def setUp(self):
        super().setUp()
        self.pos_config.open_ui()
        self.session = self.pos_config.current_session_id

    def _sync_order(self, partner=None, to_invoice=True, method=None, price=40.0):
        method = method or self.cash_method
        taxes = self.shoe.taxes_id.compute_all(price, self.session.currency_id, 1)
        total = taxes["total_included"]
        uuid = str(uuid4())
        data = {
            "amount_paid": total,
            "amount_return": 0,
            "amount_tax": total - taxes["total_excluded"],
            "amount_total": total,
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "pricelist_id": self.pos_config.pricelist_id.id,
            "name": "Order %s" % uuid,
            "last_order_preparation_change": "{}",
            "lines": [(0, 0, {
                "product_id": self.shoe.id,
                "price_unit": price,
                "qty": 1,
                "price_subtotal": taxes["total_excluded"],
                "price_subtotal_incl": total,
                "tax_ids": [(6, 0, self.shoe.taxes_id.ids)],
            })],
            "partner_id": partner.id if partner else False,
            "session_id": self.session.id,
            "payment_ids": [(0, 0, {
                "amount": total,
                "name": fields.Datetime.now(),
                "payment_method_id": method.id,
            })],
            "uuid": uuid,
            "user_id": self.env.uid,
            "to_invoice": to_invoice,
            "state": "paid",
        }
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    def test_default_sri_payment_methods(self):
        self.assertEqual(self.cash_method.l10n_ec_sri_payment_id, self.env.ref("l10n_ec.P1"))
        self.assertEqual(self.bank_method.l10n_ec_sri_payment_id, self.env.ref("l10n_ec.P20"))

    def test_receipt_without_invoice(self):
        order = self._sync_order(partner=None, to_invoice=False)
        self.assertEqual(order.state, "paid")
        self.assertFalse(order.account_move)

    def test_invoice_uses_payment_method_sri_code(self):
        self._setup_edi_company_ec()
        order = self._sync_order(partner=self.partner_dni, method=self.credit_card_method)
        invoice = order.account_move
        self.assertTrue(invoice, order.message_ids.mapped("body"))
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.l10n_ec_sri_payment_id, self.env.ref("l10n_ec.P19"))
        # Sending to the SRI is left to the scheduled job: the till never waits.
        edi_doc = invoice.edi_document_ids.filtered(
            lambda d: d.edi_format_id.code == "l10n_ec_format_sri")
        self.assertEqual(edi_doc.state, "to_send")

    def test_invoice_error_keeps_the_sale(self):
        # No certificate or emission data: the OCA module refuses to post.
        order = self._sync_order(partner=self.partner_dni)
        self.assertEqual(order.state, "paid")
        self.assertTrue(order.to_invoice)
        self.assertFalse(order.account_move)
        self.assertFalse(self.env["account.move"].search([("pos_order_ids", "in", order.ids)]))
        self.assertIn("could not be created", " ".join(order.message_ids.mapped("body")))

    def test_final_consumer_over_limit_keeps_the_sale(self):
        self._setup_edi_company_ec()
        final_consumer = self.env.ref("l10n_ec.ec_final_consumer")
        order = self._sync_order(partner=final_consumer, price=100.0)
        self.assertEqual(order.state, "paid")
        self.assertFalse(order.account_move)

    def test_back_office_invoice_still_shows_errors(self):
        order = self._sync_order(partner=self.partner_dni)
        with self.assertRaises(UserError):
            order.action_pos_order_invoice()

    def test_final_consumer_data_loaded_in_pos(self):
        final_consumer = self.env.ref("l10n_ec.ec_final_consumer")
        partner_ids = self.pos_config.get_limited_partners_loading()
        self.assertIn((final_consumer.id,), partner_ids)
        data = self.env["pos.config"]._load_pos_data_read(self.pos_config, self.pos_config)
        self.assertEqual(data[0]["_l10n_ec_final_consumer_id"], final_consumer.id)
        self.assertEqual(data[0]["_l10n_ec_final_consumer_limit"], 50.0)

    def _filter_domain(self, name):
        arch = self.env["pos.order"].get_view(
            self.env.ref("point_of_sale.view_pos_order_filter").id, "search")["arch"]
        node = etree.fromstring(arch).xpath("//filter[@name='%s']" % name)[0]
        return safe_eval(node.get("domain"))

    def test_order_filters(self):
        receipt = self._sync_order(partner=None, to_invoice=False)
        failed = self._sync_order(partner=self.partner_dni)  # not configured: fails
        orders = self.env["pos.order"]
        not_invoiced = orders.search(self._filter_domain("not_invoiced"))
        self.assertIn(receipt, not_invoiced)
        self.assertIn(failed, not_invoiced)
        invoice_failed = orders.search(self._filter_domain("invoice_failed"))
        self.assertIn(failed, invoice_failed)
        self.assertNotIn(receipt, invoice_failed)

    def test_invoice_xml_with_tax_included_prices(self):
        """Shelf prices include IVA: a 32.00 shoe is invoiced at 32.00."""
        self._setup_edi_company_ec()
        # The chart's own "IVA 15% goods" sale tax, as the store will use it.
        iva15 = self.env["account.chart.template"].with_company(self.company).ref(
            "tax_vat_15_411_goods")
        # _setup_edi_company_ec() turns the default sale tax into a 12% one;
        # put back what l10n_ec_base loads for IVA 15%.
        iva15.write({
            "price_include_override": "tax_included",
            "l10n_ec_xml_fe_code": "4",
            "tax_group_id": self.TaxGroup.search([("l10n_ec_type", "=", "vat15")], limit=1).id,
        })
        self.shoe.taxes_id = iva15
        order = self._sync_order(partner=self.partner_dni, price=32.0)
        invoice = order.account_move
        self.assertTrue(invoice, order.message_ids.mapped("body"))
        self.assertAlmostEqual(invoice.amount_total, 32.0)
        self.assertAlmostEqual(invoice.amount_tax, 4.17)
        edi_doc = invoice.edi_document_ids.filtered(
            lambda d: d.edi_format_id.code == "l10n_ec_format_sri")
        xml_string = edi_doc._l10n_ec_render_xml_edi()
        self.assertTrue(edi_doc._l10n_ec_action_check_xsd(xml_string))  # SRI schema
        xml = etree.fromstring(xml_string.encode() if isinstance(xml_string, str) else xml_string)
        self.assertAlmostEqual(float(xml.findtext(".//infoFactura/importeTotal")), 32.0)
        self.assertAlmostEqual(float(xml.findtext(".//infoFactura/totalSinImpuestos")), 27.83)
        self.assertAlmostEqual(float(xml.findtext(".//totalConImpuestos/totalImpuesto/valor")), 4.17)
        self.assertEqual(xml.findtext(".//totalConImpuestos/totalImpuesto/codigoPorcentaje"), "4")
