from uuid import uuid4

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from odoo.addons.l10n_ec_account_edi.tests.sri_response import patch_service_sri
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

    def _sync_order(self, partner=None, to_invoice=True, method=None, price=40.0, lines=None, user=None):
        """Send an order the way the till does.

        ``lines`` is a list of dicts with ``product``, ``price`` and the
        optional ``qty``, ``discount`` and ``refunded_line``; by default one
        shoe at ``price``.
        """
        method = method or self.cash_method
        lines = lines or [{"product": self.shoe, "price": price}]
        currency = self.session.currency_id
        line_vals, total, untaxed = [], 0.0, 0.0
        for line in lines:
            qty = line.get("qty", 1)
            discount = line.get("discount", 0.0)
            product = line["product"]
            unit = line["price"] * (1 - discount / 100.0)
            taxes = product.taxes_id.compute_all(unit, currency, qty, product=product)
            total += taxes["total_included"]
            untaxed += taxes["total_excluded"]
            line_vals.append((0, 0, {
                "product_id": product.id,
                "price_unit": line["price"],
                "qty": qty,
                "discount": discount,
                "price_subtotal": taxes["total_excluded"],
                "price_subtotal_incl": taxes["total_included"],
                "tax_ids": [(6, 0, product.taxes_id.ids)],
                "refunded_orderline_id": line["refunded_line"].id if line.get("refunded_line") else False,
            }))
        total = currency.round(total)
        uuid = str(uuid4())
        data = {
            "amount_paid": total,
            "amount_return": 0,
            "amount_tax": total - currency.round(untaxed),
            "amount_total": total,
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "pricelist_id": self.pos_config.pricelist_id.id,
            "name": "Order %s" % uuid,
            "last_order_preparation_change": "{}",
            "lines": line_vals,
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
        PosOrder = self.env["pos.order"].with_user(user) if user else self.env["pos.order"]
        PosOrder.sync_from_ui([data])
        # The answer also lists the refunded order, so look the order up.
        return self.env["pos.order"].search([("uuid", "=", uuid)])

    def _sri_doc(self, move):
        return move.edi_document_ids.filtered(
            lambda d: d.edi_format_id.code == "l10n_ec_format_sri")

    def _store_taxes(self):
        """IVA 15 % and 0 % as the store uses them: prices include IVA."""
        chart = self.env["account.chart.template"].with_company(self.company)
        iva15 = chart.ref("tax_vat_15_411_goods")
        iva15.write({
            "price_include_override": "tax_included",
            "l10n_ec_xml_fe_code": "4",
            "tax_group_id": self.TaxGroup.search([("l10n_ec_type", "=", "vat15")], limit=1).id,
        })
        iva0 = self.env["account.tax"].search([
            ("company_id", "=", self.company.id),
            ("type_tax_use", "=", "sale"),
            ("tax_group_id.l10n_ec_type", "=", "zero_vat"),
            ("amount", "=", 0),
        ], limit=1)
        self.assertTrue(iva0, "the Ecuadorian chart has a 0 % sale tax")
        iva0.l10n_ec_xml_fe_code = "0"
        return iva15, iva0

    def _xml(self, edi_doc):
        xml_string = edi_doc._l10n_ec_render_xml_edi()
        self.assertTrue(edi_doc._l10n_ec_action_check_xsd(xml_string))  # SRI schema
        return etree.fromstring(xml_string.encode() if isinstance(xml_string, str) else xml_string)

    def test_post_init_turns_off_receipt_self_invoicing(self):
        from odoo.addons.l10n_ec_pos_sri import _l10n_ec_pos_sri_post_init
        self.company.point_of_sale_use_ticket_qr_code = True
        _l10n_ec_pos_sri_post_init(self.env)
        self.assertFalse(self.company.point_of_sale_use_ticket_qr_code)

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
        self.assertAlmostEqual(float(xml.findtext(".//detalle/precioUnitario")), 27.83, places=2)

    @patch_service_sri
    def test_invoice_authorized_by_sri(self):
        """The scheduled job sends the invoice and stores the authorization."""
        self._setup_edi_company_ec()
        order = self._sync_order(partner=self.partner_ruc, method=self.credit_card_method)
        invoice = order.account_move
        self.assertTrue(invoice, order.message_ids.mapped("body"))
        edi_doc = self._sri_doc(invoice)
        edi_doc._process_documents_web_services(with_commit=False)
        self.assertEqual(edi_doc.state, "sent", edi_doc.error)
        self.assertTrue(invoice.l10n_ec_xml_access_key)
        self.assertTrue(invoice.l10n_ec_authorization_date)

    @patch_service_sri
    def test_refund_of_invoiced_sale_is_a_credit_note(self):
        self._setup_edi_company_ec()
        order = self._sync_order(partner=self.partner_dni)
        invoice = order.account_move
        self._sri_doc(invoice)._process_documents_web_services(with_commit=False)
        refund = self._sync_order(partner=self.partner_dni, lines=[{
            "product": self.shoe, "price": 40.0, "qty": -1,
            "refunded_line": order.lines[0],
        }])
        credit_note = refund.account_move
        self.assertTrue(credit_note, refund.message_ids.mapped("body"))
        self.assertEqual(credit_note.move_type, "out_refund")
        self.assertEqual(credit_note.reversed_entry_id, invoice)
        self.assertEqual(credit_note.l10n_latam_document_type_id.code, "04")
        edi_doc = self._sri_doc(credit_note)
        xml = self._xml(edi_doc)
        self.assertEqual(xml.tag, "notaCredito")
        self.assertEqual(xml.findtext(".//numDocModificado"), invoice.l10n_latam_document_number)
        self.assertEqual(xml.findtext(".//codDocModificado"), "01")
        self.assertTrue(xml.findtext(".//motivo"))
        edi_doc._process_documents_web_services(with_commit=False)
        self.assertEqual(edi_doc.state, "sent", edi_doc.error)

    def test_refund_of_receipt_keeps_the_return(self):
        """A return of a sale that was never invoiced cannot be a credit note."""
        self._setup_edi_company_ec()
        order = self._sync_order(partner=self.partner_dni, to_invoice=False)
        refund = self._sync_order(partner=self.partner_dni, lines=[{
            "product": self.shoe, "price": 40.0, "qty": -1,
            "refunded_line": order.lines[0],
        }])
        self.assertEqual(refund.state, "paid")
        self.assertFalse(refund.account_move)
        self.assertIn("Only a return of an invoiced sale", " ".join(refund.message_ids.mapped("body")))

    def test_invoice_with_discount_and_mixed_iva(self):
        """A discounted IVA 15 % shoe and an IVA 0 % craft item on one invoice."""
        self._setup_edi_company_ec()
        iva15, iva0 = self._store_taxes()
        self.shoe.taxes_id = iva15
        craft = self.env["product.product"].create({
            "name": "Artesania",
            "available_in_pos": True,
            "list_price": 10.0,
            "taxes_id": [(6, 0, iva0.ids)],
        })
        order = self._sync_order(partner=self.partner_dni, lines=[
            {"product": self.shoe, "price": 46.0, "discount": 10.0},  # 41.40 with IVA
            {"product": craft, "price": 10.0, "qty": 2},              # 20.00, no IVA
        ])
        invoice = order.account_move
        self.assertTrue(invoice, order.message_ids.mapped("body"))
        self.assertAlmostEqual(invoice.amount_total, 61.40)
        xml = self._xml(self._sri_doc(invoice))
        self.assertAlmostEqual(float(xml.findtext(".//infoFactura/importeTotal")), 61.40)
        rates = {
            t.findtext("codigoPorcentaje"): float(t.findtext("valor"))
            for t in xml.iterfind(".//infoFactura/totalConImpuestos/totalImpuesto")
        }
        self.assertAlmostEqual(rates["4"], invoice.amount_tax)
        self.assertAlmostEqual(rates["0"], 0.0)
        self.assertAlmostEqual(float(xml.findtext(".//totalDescuento")), 4.0)
        # Every line adds up the way the SRI checks it, without IVA.
        for detail in xml.iterfind(".//detalles/detalle"):
            qty, unit, discount, subtotal = (float(detail.findtext(tag)) for tag in (
                "cantidad", "precioUnitario", "descuento", "precioTotalSinImpuesto"))
            self.assertAlmostEqual(qty * unit - discount, subtotal, places=2)
            self.assertAlmostEqual(subtotal, float(detail.findtext(".//impuesto/baseImponible")), places=2)
        shoe_line = next(d for d in xml.iterfind(".//detalles/detalle") if float(d.findtext("descuento")))
        self.assertAlmostEqual(float(shoe_line.findtext("precioUnitario")), 40.0)  # 46.00 / 1.15
        self.assertAlmostEqual(float(shoe_line.findtext("precioTotalSinImpuesto")), 36.0)

    def test_invoice_from_till_with_pos_user_rights(self):
        """The store till logs in as a user with only Point of Sale: User rights."""
        self._setup_edi_company_ec()
        cashier = self.env["res.users"].create({
            "name": "Caja",
            "login": "caja_ec",
            "company_id": self.company.id,
            "company_ids": [(6, 0, self.company.ids)],
            "group_ids": [(6, 0, [self.env.ref("point_of_sale.group_pos_user").id,
                                  self.env.ref("base.group_user").id])],
        })
        order = self._sync_order(partner=self.partner_dni, user=cashier)
        self.assertTrue(order.account_move, order.message_ids.mapped("body"))
        self.assertEqual(order.account_move.state, "posted")
        # A failing invoice is reported on the order under the same rights.
        self.company.l10n_ec_key_type_id = False
        failed = self._sync_order(partner=self.partner_dni, user=cashier)
        self.assertEqual(failed.state, "paid")
        self.assertFalse(failed.account_move)
        self.assertIn("could not be created", " ".join(failed.message_ids.mapped("body")))
