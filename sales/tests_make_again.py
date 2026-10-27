"""FB-27 (Ahmed's note #27, undo): a cancelled invoice is made again in one step, as a new draft with the same lines."""

from decimal import Decimal as D

from django.test import TestCase

from hesba_testing.factories import add_purchase_line, make_draft_purchase_invoice, make_user, posted_invoice_ready, recalculate_purchase_totals
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.tests_dashboard import prepared_client

from .services import cancel_posted_sales_invoice, post_sales_invoice


class MakeAgainTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = make_user("owner_again", is_superuser=True)
        self.client.force_login(self.owner)

    def test_a_cancelled_sale_opens_the_invoice_form_with_its_lines(self):
        invoice, item, location, cashbox = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk, self.owner)
        self.assertNotContains(self.client.get(f"/sales/{invoice.pk}/"), "data-make-again")  # a posted one is cancelled, not copied
        cancel_posted_sales_invoice(invoice.pk, self.owner, "wrong date")
        self.assertContains(self.client.get(f"/sales/{invoice.pk}/"), f"copy={invoice.pk}")
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        form = page.context["form"]
        self.assertEqual((form.initial["customer"], form.initial["selling_location"], form.initial["cashbox"], form.initial["paid_now"]),
                         (invoice.customer_id, location.pk, cashbox.pk, D("60.00")))
        self.assertIn(invoice.invoice_number, form.initial["notes"])
        lines = page.context["line_formset"].initial
        self.assertEqual([(row["item"], row["quantity"], row["unit_sale_price"]) for row in lines], [(item, D("2.000"), D("30.00"))])

    def test_only_a_cancelled_invoice_is_copied(self):
        invoice, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual(page.context["line_formset"].initial, None)

    def test_a_cancelled_purchase_too(self):
        purchase = make_draft_purchase_invoice(invoice_number="PI-AGAIN")
        from hesba_testing.factories import make_item

        item = make_item("AGAIN-1")
        add_purchase_line(purchase, item, 4, "25.00")
        recalculate_purchase_totals(purchase)
        post_purchase_invoice(purchase.pk, self.owner)
        cancel_posted_purchase_invoice(purchase.pk, self.owner, "wrong supplier")
        self.assertContains(self.client.get(f"/purchases/{purchase.pk}/"), "data-make-again")
        page = self.client.get(f"/purchases/new/?copy={purchase.pk}")
        self.assertEqual(page.context["form"].initial["supplier"], purchase.supplier_id)
        self.assertEqual([(row["item"], row["quantity"], row["unit_purchase_price"]) for row in page.context["line_formset"].initial],
                         [(item, D("4.000"), D("25.00"))])
