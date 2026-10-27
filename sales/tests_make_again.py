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


class MakeAgainKeepsTests(MakeAgainTests):
    """Codex #284: the copy spends points again apart from its discount, keeps a hand-typed tax, serials and batches."""

    def cancelled_sale(self):
        invoice, item, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "again")
        return type(invoice).objects.get(pk=invoice.pk), item

    def test_points_spent_come_apart_from_the_discount(self):
        from loyalty.models import InvoiceRedemption

        invoice, _item = self.cancelled_sale()
        type(invoice).objects.filter(pk=invoice.pk).update(discount_amount=D("15.00"))
        InvoiceRedemption.objects.create(invoice=invoice, points=100, amount=D("10.00"))
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual(page.context["form"].initial["discount_amount"], D("5.00"))  # the ordinary discount only
        self.assertEqual(page.context["redeem_points"], "100")  # spent again through the usual check on saving

    def test_a_hand_typed_tax_and_the_serials_are_kept(self):
        from django.utils import timezone

        from serials.models import SerialNumber, SerialSale

        invoice, item = self.cancelled_sale()
        type(invoice).objects.filter(pk=invoice.pk).update(tax_amount=D("8.40"))
        line = invoice.lines.get()
        for serial in ("SN-1", "SN-2"):
            SerialSale.objects.create(serial=SerialNumber.objects.create(item=item, serial=serial, received_on=timezone.localdate()), sales_line=line)
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual(page.context["form"].initial["tax_amount"], D("8.40"))
        self.assertEqual(page.context["line_formset"].initial[0]["serials"], "SN-1, SN-2")  # a one-line input keeps commas
        from serials.services import parse_serials

        self.assertEqual(parse_serials("SN-1, SN-2"), ["SN-1", "SN-2"])

    def test_a_purchase_keeps_its_batch_expiry_and_tax(self):
        from datetime import date

        from batches.models import Batch
        from hesba_testing.factories import make_item

        purchase = make_draft_purchase_invoice(invoice_number="PI-BATCH")
        item = make_item("BATCH-1")
        line = add_purchase_line(purchase, item, 4, "25.00")
        recalculate_purchase_totals(purchase)
        post_purchase_invoice(purchase.pk, self.owner)
        Batch.objects.create(item=item, location=purchase.receiving_location, batch_no="LOT-7", expiry_date=date(2027, 5, 1), quantity=4,
                             received_on=purchase.invoice_date, purchase_line=line)
        cancel_posted_purchase_invoice(purchase.pk, self.owner, "again")
        type(purchase).objects.filter(pk=purchase.pk).update(tax_amount=D("14.00"))
        page = self.client.get(f"/purchases/new/?copy={purchase.pk}")
        row = page.context["line_formset"].initial[0]
        self.assertEqual((row["batch_no"], row["expiry_date"]), ("LOT-7", date(2027, 5, 1)))
        self.assertEqual(page.context["form"].initial["tax_amount"], D("14.00"))

    def test_a_promotions_discount_is_worked_out_again_not_copied(self):
        from promotions.models import InvoicePromotion, Promotion, PromotionKind

        invoice, item = self.cancelled_sale()
        invoice.lines.update(line_discount_amount=D("6.00"))  # 10% of the line's 60: the promotion's share
        promo = Promotion.objects.create(name="خصم الكوبون", kind=PromotionKind.PERCENT, coupon_code="EID10", percent=D("10"), item=item)
        InvoicePromotion.objects.create(invoice=invoice, promotion=promo, discount=D("10.00"), coupon_code="EID10")
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual([row["line_discount_amount"] for row in page.context["line_formset"].initial], [0])  # not stacked on save
        self.assertEqual(page.context["coupon"], "EID10")  # the same coupon applies it once again
        invoice.lines.update(line_discount_amount=D("20.00"))  # typed by hand, not the promotion's 6
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual([row["line_discount_amount"] for row in page.context["line_formset"].initial], [D("20.00")])

class MakeAgainNeedsViewTests(MakeAgainTests):
    def test_copying_needs_the_permission_to_see_invoices(self):
        from hesba_testing.factories import grant, make_role, make_user_profile
        from permissions.models import Permission

        invoice, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "again")
        role = make_role(code="MAKER-ONLY", name_ar="بياع", name_en="Seller")
        grant(role, Permission.objects.get(code="sales.create_sales_invoice"))
        seller = make_user("maker_only")
        make_user_profile(user=seller, role=role)
        self.client.force_login(seller)
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual(page.context["line_formset"].initial, None)
        self.assertNotIn(invoice.invoice_number, page.context["form"].initial.get("notes", ""))

