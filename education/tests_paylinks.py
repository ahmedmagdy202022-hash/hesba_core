"""E4a: pay fees by link. Entry points on the fees and dues screens for ``payments.services.create_link``, which is called unchanged.

Every money test states exact amounts. The tests also prove what the entry points must not do: making a link moves no money
(journal entries, cashbox movements and customer payments are counted before and after); only the gateway's verified
callback does, through the existing S4 code (the mock provider; no key, no network, no spend).
"""

import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock
from urllib.parse import quote, unquote

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_location, make_seeded_role, make_user, make_user_profile
from ledger.models import JournalEntry
from parties.services import balance
from payments import providers, services as pay
from payments.models import LinkStatus, PaymentLink
from payments.providers import GatewayError
from payments.providers.mock import MockPaymob
from payments.tests import BASE, GATEWAY_SETTINGS
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerPayment, SalesInvoice
from sales.services import record_customer_payment
from settings_core.capabilities import set_capability_enabled
from settlements.services import settle

from . import billing, paylinks, services
from .models import FeeCharge

CENTER = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"
TODAY = timezone.localdate()
MONTH = TODAY.replace(day=1)


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def moves():
    """What a payment link must never change."""

    return {"journal": JournalEntry.objects.count(), "cashbox": CashboxMovement.objects.count(), "payments": CustomerPayment.objects.count()}


@override_settings(**GATEWAY_SETTINGS)
class PayFeesSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client("education", "tutoring_center", CENTER)
        self.owner = person(RoleCode.OWNER, "e4a_owner")
        make_location(location_code="CENTER", is_default=True)
        self.cash = make_cashbox(cashbox_code="CASH", is_default=True)
        self.gateway = make_cashbox(cashbox_code="GW", name_ar="بوابة الدفع – Paymob", name_en="Payment gateway", kind="bank")
        set_capability_enabled(self.profile, "payment_links", True, user=self.owner)  # off by default
        self.month = MONTH
        self.count = 0
        self.client.force_login(self.owner)

    def enrolled(self, name, fee, phone="01001234501", payer="محمد السيد"):
        """A student of a new monthly group at ``fee``. ``payer`` is a name, or a customer (so two children can share a parent)."""

        self.count += 1
        course = services.save_course({"name": f"كورس {self.count}", "fee": str(fee), "basis": "monthly"}, self.owner)
        group = services.save_group({"course": course, "name": f"مجموعة {self.count}", "days": "sat"}, self.owner)
        who = {"payer": payer} if not isinstance(payer, str) else {"payer_name": payer, "payer_phone": phone}
        student = services.save_student({"name": name, **who}, self.owner)
        services.enroll(student, group, self.owner, start_date=self.month)
        return student

    def fee_invoice(self, name, fee, phone="01001234501", payer="محمد السيد", post=True):
        """One student, one monthly group at ``fee``, billed on its own: a fee invoice of exactly ``fee``."""

        self.enrolled(name, fee, phone, payer)
        (invoice,) = billing.bill_month(self.month, self.owner, post=post)
        invoice.refresh_from_db()
        return invoice

    def pay_off(self, invoice, amount, settled=True):
        """Money received from the parent: at the desk as a plain customer payment, and (``settled``) tied to the invoice the way a
        deposit or a paid link is."""

        self.count += 1
        payment = record_customer_payment(f"CP-E4A-{self.count}", TODAY, invoice.customer, self.cash, D(amount), self.owner)
        if settled:
            settle(invoice, payment, D(amount), "deposit")
        return payment

    def fees_url(self, lang="ar"):
        return f"{reverse('education:fees')}?month={self.month:%Y-%m}&lang={lang}"

    def make(self, invoice, lang="ar", client=None, **extra):
        return (client or self.client).post(reverse("education:paylink_make", args=[invoice.pk]), {"lang": lang, **extra})

    def live(self, invoice):
        return PaymentLink.objects.filter(invoice=invoice, status=LinkStatus.NEW).first()

    def message_texts(self, response):
        """The message that request queued (earlier unread ones are not its business)."""

        from django.contrib.messages import get_messages

        return [str(message) for message in get_messages(response.wsgi_request)][-1:]

    def user_with(self, username, *codes):
        from permissions import role_services

        role = role_services.create_role(name_ar=f"دور {username}", codes=list(codes), actor=self.owner)
        user = make_user(username=username)
        make_user_profile(user=user, role=role)
        client = Client()
        client.force_login(user)
        return user, client

    def page_url(self, page):
        """The link as the page shows it (the signed address carries a timestamp, so it is read, never rebuilt)."""

        import html
        import re

        found = re.search(r'data-copy-link="([^"]+)"', page.content.decode())
        return html.unescape(found.group(1)) if found else ""


class FeesScreenTests(PayFeesSetup):
    def test_1250_with_500_paid_asks_750_and_the_page_shows_it(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.assertEqual(invoice.total_amount, D("1250.00"))
        self.pay_off(invoice, "500")
        page = self.client.get(self.fees_url())
        self.assertContains(page, f'data-pay-invoice="{invoice.pk}"')
        self.assertContains(page, 'data-pay-state="ready"')
        self.assertContains(page, "data-pay-make")
        response = self.make(invoice)
        self.assertEqual(response.status_code, 302)
        link = self.live(invoice)
        self.assertEqual((link.amount, link.status, link.customer, link.cashbox, link.created_by), (D("750.00"), LinkStatus.NEW, invoice.customer, self.gateway, self.owner))
        page = self.client.get(response["Location"])
        self.assertContains(page, 'data-pay-state="live"')
        self.assertContains(page, "750.00")
        self.assertNotContains(page, "1,250.00 ·")  # the link is not for the invoice's total
        self.assertEqual(page.context["messages"] and [str(m) for m in page.context["messages"]], ["اتعمل لينك الدفع؛ ابعته لولي الأمر."])
        self.assertContains(page, "data-copy-link=")
        self.assertContains(page, "بينتهي بعد 3 أيام")  # the expiry in words (72 hours), and the exact time beside it
        self.assertContains(page, f"{timezone.localtime(link.expires_at):%Y-%m-%d %H:%M}")

    def test_a_desk_payment_that_is_not_tied_to_the_invoice_still_lowers_the_ask(self):
        # Fees are usually collected as plain customer payments, which do not change an invoice's own «outstanding»; the link never asks more
        # than the parent's account still owes, so a parent who paid 500 at the desk is asked for 750 and one who paid all is asked for nothing.
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.pay_off(invoice, "500", settled=False)
        self.assertEqual(pay.outstanding(invoice), D("1250.00"))  # the invoice's own figure is unchanged
        self.make(invoice)
        self.assertEqual(self.live(invoice).amount, D("750.00"))
        other = self.fee_invoice("مريم سمير", "400", payer="سمير علي", phone="01001234502")
        self.pay_off(other, "400", settled=False)
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, f'data-pay-invoice="{other.pk}"')
        response = self.make(other)
        self.assertEqual(self.message_texts(response), [pay.MESSAGES["ar"]["nothing_due"]])
        self.assertFalse(PaymentLink.objects.filter(invoice=other).exists())

    def test_a_parent_with_two_invoices_is_asked_for_what_is_owed_newest_first(self):
        old = self.fee_invoice("يوسف محمد", "600")
        parent = old.customer
        self.enrolled("مريم محمد", "450", payer=parent)
        (new,) = billing.bill_month(self.month, self.owner, post=True)
        self.assertEqual(new.customer_id, parent.pk)
        self.pay_off(old, "200", settled=False)  # 1,050 billed, 200 paid at the desk: 850 owed
        board = paylinks.Board(self.owner, "ar").load()
        self.assertEqual({board.states[old.pk].ask, board.states[new.pk].ask}, {D("400.00"), D("450.00")})
        self.assertEqual(board.states[new.pk].ask + board.states[old.pk].ask, balance("customer", parent))  # never more than the account owes
        page = self.client.get(reverse("education:dues"))
        self.assertEqual(page.content.decode().count("data-pay-line="), 2)

    def test_a_draft_offers_post_it_first_and_a_crafted_post_gets_the_existing_message(self):
        draft = self.fee_invoice("يوسف محمد", "300", post=False)
        page = self.client.get(self.fees_url())
        self.assertContains(page, "data-pay-post-first")
        self.assertContains(page, "رحّلها الأول")
        self.assertNotContains(page, "data-pay-make")
        response = self.make(draft)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.message_texts(response), [pay.MESSAGES["ar"]["not_posted"]])
        self.assertFalse(PaymentLink.objects.exists())
        english = self.make(draft, lang="en")
        self.assertEqual(self.message_texts(english), [pay.MESSAGES["en"]["not_posted"]])

    def test_a_fully_paid_invoice_offers_nothing_and_a_crafted_post_gets_nothing_due(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.pay_off(invoice, "1250")
        self.assertEqual(pay.outstanding(invoice), D("0.00"))
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-make")
        self.assertNotContains(page, 'data-pay-state="live"')
        response = self.make(invoice)
        self.assertEqual(self.message_texts(response), [pay.MESSAGES["ar"]["nothing_due"]])
        self.assertFalse(PaymentLink.objects.exists())

    def test_a_second_click_shows_the_same_live_link_and_create_link_itself_refuses_a_second(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.make(invoice)
        first = self.live(invoice)
        response = self.make(invoice)
        self.assertEqual(self.message_texts(response), ["فيه لينك دفع شغال للفاتورة دي بالفعل؛ ده هو."])
        self.assertEqual([link.pk for link in PaymentLink.objects.filter(invoice=invoice)], [first.pk])  # reused: same row, no new gateway order
        with self.assertRaisesMessage(ValidationError, pay.MESSAGES["ar"]["live"]):  # what create_link does today: refuse, never replace
            pay.create_link(invoice, self.owner)
        self.assertEqual(PaymentLink.objects.filter(invoice=invoice).count(), 1)
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-make")  # a live link is shown instead of the button
        self.assertContains(page, "data-pay-live")

    def test_the_callback_confirming_750_pays_the_invoice_through_the_existing_path(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.pay_off(invoice, "500")
        self.make(invoice)
        link = self.live(invoice)
        before = CustomerPayment.objects.count()
        provider = providers.active()
        self.assertIsInstance(provider, MockPaymob)
        payload, signature = provider.callback(link.gateway_order_id, pay.to_cents(link.amount), merchant_reference=link.reference)
        response = Client(enforce_csrf_checks=True).post(f"{reverse('pay_webhook_paymob')}?hmac={signature}", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        payment = CustomerPayment.objects.exclude(payment_number__startswith="CP-E4A").get()
        self.assertEqual(CustomerPayment.objects.count(), before + 1)
        self.assertEqual((payment.amount, payment.cashbox, payment.customer), (D("750.00"), self.gateway, invoice.customer))
        invoice.refresh_from_db()
        self.assertEqual(pay.outstanding(invoice), D("0.00"))
        self.assertEqual(balance("customer", invoice.customer), D("0.00"))
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.PAID)
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-make")  # nothing due any more: no action

    def test_making_links_moves_no_money(self):
        invoices = [self.fee_invoice(f"طالب {i}", "400", payer=f"ولي {i}", phone=f"0100123450{i}") for i in range(3)]
        before = moves()
        self.make(invoices[0])
        self.make(invoices[0])  # a repeat
        self.client.post(reverse("education:paylinks_bulk"), {"month": f"{self.month:%Y-%m}", "lang": "ar"})
        self.assertEqual(PaymentLink.objects.count(), 3)
        self.assertEqual(moves(), before)
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), 3)


class WhatsAppTests(PayFeesSetup):
    def link_and_text(self, invoice, lang):
        self.make(invoice, lang=lang)
        link = self.live(invoice)
        page = self.client.get(self.fees_url(lang))
        content = page.content.decode()
        href = next(part.split('"')[0] for part in content.split('href="') if part.startswith("https://wa.me/"))
        return link, page, href, self.page_url(page)

    def test_the_arabic_message_is_exact_and_the_phone_is_normalised(self):
        invoice = self.fee_invoice("يوسف محمد", "1250", phone="٠١٠٠١٢٣٤٥٦٧")  # Arabic digits typed by staff
        self.pay_off(invoice, "500")
        link, page, href, url = self.link_and_text(invoice, "ar")
        self.assertEqual(pay.read_token(url.split("/pay/")[1].split("/")[0]), link)  # the address in the message opens this very link
        expected = f"أهلاً محمد السيد، باقي 750.00 ج.م من مصروفات يوسف محمد عن {billing.month_label(self.month, 'ar')}. تقدر تدفعها أونلاين من هنا: {url}"
        self.assertEqual(href, f"https://wa.me/201001234567?text={quote(expected)}")
        self.assertEqual(unquote(href.split("?text=")[1]), expected)
        self.assertContains(page, "ابعته واتساب")

    def test_the_english_message_is_exact(self):
        invoice = self.fee_invoice("Youssef Mohamed", "1250", payer="Mohamed Elsayed", phone="01001234567")
        self.pay_off(invoice, "500")
        link, page, href, url = self.link_and_text(invoice, "en")
        self.assertTrue(url.endswith("?lang=en"))
        expected = f"Hello Mohamed Elsayed, 750.00 EGP is left on the fees of Youssef Mohamed for {billing.month_label(self.month, 'en')}. You can pay online here: {url}"
        self.assertEqual(unquote(href.split("?text=")[1]), expected)
        self.assertTrue(href.startswith("https://wa.me/201001234567?text="))
        self.assertContains(page, "Send on WhatsApp")
        self.assertContains(page, "Expires in 3 days")

    def test_one_invoice_for_two_children_names_both(self):
        first = self.enrolled("يوسف محمد", "600")
        self.enrolled("مريم محمد", "450", payer=first.payer)
        (invoice,) = billing.bill_month(self.month, self.owner, post=True)
        link, page, href, url = self.link_and_text(invoice, "ar")
        text = unquote(href.split("?text=")[1])
        self.assertTrue("من مصروفات يوسف محمد، مريم محمد عن" in text or "من مصروفات مريم محمد، يوسف محمد عن" in text)
        self.assertIn("باقي 1,050.00 ج.م", text)

    def test_no_phone_means_no_whatsapp_link_but_the_copy_button_still_works(self):
        invoice = self.fee_invoice("يوسف محمد", "1250", payer=make_customer(customer_code="C-NOPHONE", name="ولي بلا رقم", phone=""))
        self.make(invoice)
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-whatsapp")
        self.assertNotContains(page, "https://wa.me/")
        self.assertContains(page, "data-pay-no-phone")
        self.assertTrue(self.page_url(page).startswith(f"{BASE}/pay/"))


class BulkTests(PayFeesSetup):
    def invoices(self):
        a = self.fee_invoice("أحمد", "600", payer="ولي أ", phone="01001234501")
        b = self.fee_invoice("باسم", "450", payer=make_customer(customer_code="C-NOPHONE", name="ولي ب", phone=""))  # no phone is not a reason to skip
        paid = self.fee_invoice("جمال", "300", payer="ولي ج", phone="01001234503")
        self.pay_off(paid, "300")
        draft = self.fee_invoice("دينا", "250", payer="ولي د", phone="01001234504", post=False)
        return a, b, paid, draft

    def run_bulk(self, **extra):
        return self.client.post(reverse("education:paylinks_bulk"), {"month": f"{self.month:%Y-%m}", "lang": "ar", **extra}, follow=True)

    def test_bulk_makes_two_links_and_reports_the_draft_and_the_paid_one_as_skipped(self):
        a, b, paid, draft = self.invoices()
        before = moves()
        page = self.run_bulk()
        self.assertEqual(page.redirect_chain[-1][0].split("?")[0], reverse("education:paylinks_result"))
        self.assertEqual(sorted(PaymentLink.objects.values_list("amount", flat=True)), [D("450.00"), D("600.00")])
        self.assertEqual({link.invoice_id for link in PaymentLink.objects.all()}, {a.pk, b.pk})
        self.assertEqual(moves(), before)
        self.assertContains(page, 'data-pay-made>2<')
        self.assertContains(page, 'data-pay-skipped>2<')
        self.assertContains(page, 'data-pay-failed-count>0<')
        self.assertContains(page, "مسودة (رحّلها الأول)")
        self.assertContains(page, "مفيش عليها باقي")
        self.assertContains(page, "ولسه ماتبعتتش لحد")  # bulk only makes links; sending stays one tap per parent
        self.assertContains(page, "data-pay-whatsapp")  # the guardian with a phone gets her button
        self.assertContains(page, "مفيش رقم تليفون")  # the one without still has her link, with a copy button
        self.assertNotContains(page, "data-pay-left")
        # A refresh of the result never makes more links.
        self.client.get(page.redirect_chain[-1][0])
        self.assertEqual(PaymentLink.objects.count(), 2)
        # Running it again skips the two that now have a live link.
        again = self.run_bulk()
        self.assertEqual(PaymentLink.objects.count(), 2)
        self.assertContains(again, "عليها لينك شغال بالفعل")
        self.assertContains(again, 'data-pay-made>0<')

    def test_bulk_stops_at_the_cap_and_says_how_many_are_left(self):
        for i in range(4):
            self.fee_invoice(f"طالب {i}", "100", payer=f"ولي {i}", phone=f"0100123450{i}")
        with mock.patch.object(paylinks, "BULK_CAP", 3):
            page = self.run_bulk()
        self.assertEqual(PaymentLink.objects.count(), 3)
        self.assertContains(page, "data-pay-left")
        self.assertContains(page, "فاضل 1 فاتورة")
        again = self.run_bulk()  # the next click carries on with what is left, not with the ones already made
        self.assertEqual(PaymentLink.objects.count(), 4)
        self.assertNotContains(again, "data-pay-left")
        self.assertContains(again, 'data-pay-made>1<')

    def test_the_time_budget_stops_new_links_too(self):
        for i in range(3):
            self.fee_invoice(f"طالب {i}", "100", payer=f"ولي {i}", phone=f"0100123450{i}")
        result = paylinks.bulk(self.month, self.owner, "ar", cap=10, seconds=-1)  # already out of time after the first
        self.assertEqual((len(result["made"]), result["left"]), (1, 2))

    def test_one_failing_link_does_not_undo_the_others_and_is_listed_by_student(self):
        a, b, paid, draft = self.invoices()
        calls = {"n": 0}
        real = MockPaymob._http

        def flaky(provider, method, path, body):
            if path == "/api/ecommerce/orders":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise GatewayError("down")
            return real(provider, method, path, body)

        with mock.patch.object(MockPaymob, "_http", flaky):
            page = self.run_bulk()
        self.assertEqual(PaymentLink.objects.count(), 1)  # the failed one left no row behind
        failed_invoice = next(invoice for invoice in (a, b) if not PaymentLink.objects.filter(invoice=invoice).exists())
        self.assertContains(page, 'data-pay-made>1<')
        self.assertContains(page, 'data-pay-failed-count>1<')
        self.assertContains(page, "data-pay-failed-list")
        self.assertContains(page, FeeCharge.objects.get(invoice=failed_invoice).enrollment.student.name)
        self.assertContains(page, pay.MESSAGES["ar"]["gateway"])

    def test_the_result_screen_in_english(self):
        self.invoices()
        page = self.client.post(reverse("education:paylinks_bulk"), {"month": f"{self.month:%Y-%m}", "lang": "en"}, follow=True)
        self.assertContains(page, "Payment links for the month")
        self.assertContains(page, "Draft (post it first)")
        self.assertContains(page, "nobody has been sent one yet")
        self.assertContains(page, 'dir="ltr"')

    def test_bulk_needs_the_month_and_the_right(self):
        response = self.client.post(reverse("education:paylinks_bulk"), {"lang": "ar"}, follow=True)
        self.assertContains(response, billing.MESSAGES["ar"]["month"])
        self.assertFalse(PaymentLink.objects.exists())


class PermissionTests(PayFeesSetup):
    def test_without_the_right_there_is_no_button_and_a_crafted_post_is_refused(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        _, client = self.user_with("e4a_viewer", "sales.view_sales_invoices", "reports.view_customer_report")  # opens both screens, may not collect
        page = client.get(self.fees_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f"{invoice.invoice_number}")  # the screen is theirs, the action is not
        for marker in ("data-pay-make", "data-pay-state", "data-pay-bulk"):
            self.assertNotContains(page, marker)
        self.assertNotContains(client.get(reverse("education:dues")), "data-pay-payer")
        self.assertEqual(self.make(invoice, client=client).status_code, 403)
        self.assertEqual(client.post(reverse("education:paylinks_bulk"), {"month": f"{self.month:%Y-%m}"}).status_code, 403)
        self.assertEqual(client.get(reverse("education:paylinks_result")).status_code, 403)
        self.assertFalse(PaymentLink.objects.exists())

    def test_the_owner_sees_the_button(self):
        self.fee_invoice("يوسف محمد", "1250")
        self.assertContains(self.client.get(self.fees_url()), "data-pay-make")

    def test_get_is_not_allowed_on_the_making_views(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.assertEqual(self.client.get(reverse("education:paylink_make", args=[invoice.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("education:paylinks_bulk")).status_code, 405)

    def test_another_entitys_invoice_and_a_non_fee_invoice_are_not_found(self):
        from entities.models import Entity

        invoice = self.fee_invoice("يوسف محمد", "1250")
        branch = Entity.objects.create(code="E-EDU-PL", name_ar="فرع تاني", activity_slug="education", sub_activity_slug="tutoring_center")
        session = self.client.session
        session["hesba_entity"] = branch.pk
        session.save()
        self.assertEqual(self.make(invoice).status_code, 404)
        self.assertEqual(self.client.post(reverse("education:paylinks_bulk"), {"month": f"{self.month:%Y-%m}", "lang": "ar"}).status_code, 302)
        self.assertFalse(PaymentLink.objects.exists())
        self.assertNotContains(self.client.get(self.fees_url()), f'data-pay-invoice="{invoice.pk}"')
        session["hesba_entity"] = None
        session.save()
        self.assertEqual(self.make(invoice).status_code, 302)  # the owning entity can

    def test_an_invoice_that_is_not_a_fee_invoice_is_not_found(self):
        from master_data.models import Location
        from hesba_testing.factories import make_item
        from sales.services import create_sales_draft, post_sales_invoice

        draft = create_sales_draft(
            {"invoice_number": "SI-BOOK", "invoice_date": TODAY, "customer": make_customer(customer_code="C-BOOK", name="عميل كتب", phone="01001234599"),
             "selling_location": Location.objects.get(location_code="CENTER"), "cashbox": None, "discount_amount": D("0"), "tax_amount": D("0"), "paid_now": D("0")},
            [{"item": make_item(item_code="BOOK", is_stock_tracked=False), "quantity": D("1"), "unit_sale_price": D("90.00"), "line_discount_amount": D("0"), "description": "كتاب"}],
            self.owner)
        post_sales_invoice(draft.pk, self.owner)
        self.assertEqual(self.make(draft).status_code, 404)  # the screens list fee invoices only, so this endpoint makes links for those only
        self.assertFalse(PaymentLink.objects.exists())


class OffTests(PayFeesSetup):
    def test_online_payment_off_offers_no_action_and_the_owner_gets_one_line_saying_where(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        set_capability_enabled(self.profile, "payment_links", False, user=self.owner)
        page = self.client.get(self.fees_url())
        for marker in ("data-pay-make", "data-pay-state", "data-pay-bulk"):
            self.assertNotContains(page, marker)
        self.assertContains(page, "data-pay-note")
        self.assertContains(page, pay.MESSAGES["ar"]["off"])
        self.assertContains(page, reverse("settings_core:capabilities"))
        english = self.client.get(self.fees_url("en"))
        self.assertContains(english, pay.MESSAGES["en"]["off"])
        dues = self.client.get(reverse("education:dues"))
        self.assertContains(dues, "data-pay-note")
        self.assertNotContains(dues, "data-pay-payer")
        response = self.make(invoice)
        self.assertEqual(self.message_texts(response), [pay.MESSAGES["ar"]["off"]])  # a crafted POST gets create_link's own refusal
        self.assertFalse(PaymentLink.objects.exists())
        # Someone who can collect but cannot set it up sees no note.
        _, client = self.user_with("e4a_cash", "sales.view_sales_invoices", "sales.receive_customer_payment")
        self.assertNotContains(client.get(self.fees_url()), "data-pay-note")

    def test_no_provider_connected_says_so_and_offers_nothing(self):
        self.fee_invoice("يوسف محمد", "1250")
        with override_settings(PAYMENT_GATEWAY_MOCK=False):
            page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-make")
        self.assertContains(page, pay.MESSAGES["ar"]["not_connected"])

    def test_nothing_is_said_when_there_is_nothing_to_pay(self):
        set_capability_enabled(self.profile, "payment_links", False, user=self.owner)
        self.assertNotContains(self.client.get(self.fees_url()), "data-pay-note")  # no invoice with money due, so no nagging

    def test_a_missing_gateway_account_is_said_once_not_on_every_row(self):
        self.fee_invoice("يوسف محمد", "1250")
        self.fee_invoice("مريم", "400", payer="سمير", phone="01001234502")
        self.gateway.delete()
        page = self.client.get(self.fees_url())
        self.assertNotContains(page, "data-pay-make")
        self.assertEqual(page.content.decode().count(pay.MESSAGES["ar"]["no_box"]), 1)


class DuesScreenTests(PayFeesSetup):
    def test_each_payers_open_invoices_get_the_action_in_a_compact_list(self):
        one = self.fee_invoice("يوسف محمد", "600")
        two = self.fee_invoice("مريم محمد", "450")
        page = self.client.get(reverse("education:dues"))
        self.assertContains(page, f'data-due="{one.customer_id}"')
        self.assertContains(page, "data-pay-payer")
        for invoice in (one, two):
            self.assertContains(page, f'data-pay-line="{invoice.pk}"')
        self.assertContains(page, "data-pay-make")
        response = self.client.post(reverse("education:paylink_make", args=[one.pk]), {"lang": "ar", "next": f"{reverse('education:dues')}?lang=ar#pay-{one.pk}"})
        self.assertEqual(response["Location"], f"{reverse('education:dues')}?lang=ar#pay-{one.pk}")
        page = self.client.get(response["Location"])
        self.assertContains(page, "data-pay-live")
        self.assertContains(page, "ابعته واتساب")
        self.assertEqual(PaymentLink.objects.get().amount, one.total_amount)

    def test_an_outside_next_address_is_ignored(self):
        invoice = self.fee_invoice("يوسف محمد", "600")
        response = self.client.post(reverse("education:paylink_make", args=[invoice.pk]), {"lang": "ar", "next": "https://evil.example/x"})
        self.assertTrue(response["Location"].startswith(reverse("education:fees")))

    def test_the_dues_screen_in_english(self):
        self.fee_invoice("Youssef", "600", payer="Mohamed")
        page = self.client.get(reverse("education:dues") + "?lang=en")
        self.assertContains(page, "Payment link")
        self.assertContains(page, 'dir="ltr"')


class ScreenTests(PayFeesSetup):
    def test_the_fees_screen_in_arabic_and_english_with_the_bulk_form(self):
        self.fee_invoice("يوسف محمد", "1250")
        ar = self.client.get(self.fees_url("ar"))
        self.assertContains(ar, "لينك دفع")
        self.assertContains(ar, "اعمل لينكات دفع للشهر")
        self.assertContains(ar, f"لحد {paylinks.BULK_CAP} في المرة")
        self.assertContains(ar, "مبيبعتش حاجة")
        self.assertContains(ar, 'data-pay-bulk-ready="1"')
        en = self.client.get(self.fees_url("en"))
        self.assertContains(en, "Make payment links for the month")
        self.assertContains(en, "It sends nothing")
        self.assertContains(en, 'dir="ltr"')
        self.assertNotContains(en, "data-label=")

    def test_nothing_ready_means_no_bulk_form(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.pay_off(invoice, "1250")
        self.assertNotContains(self.client.get(self.fees_url()), "data-pay-bulk")

    def test_a_stale_live_link_is_flagged_and_can_be_cancelled(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.make(invoice)
        self.pay_off(invoice, "500", settled=False)  # paid at the desk after the link went out
        page = self.client.get(self.fees_url())
        self.assertContains(page, "data-pay-stale")
        self.assertContains(page, "data-pay-cancel")
        self.assertNotContains(page, "data-pay-whatsapp")  # a link that asks too much is not offered for sending
        link = self.live(invoice)
        self.client.post(reverse("payments:cancel", args=[link.pk]), {"lang": "ar"})
        link.refresh_from_db()
        self.assertEqual(link.status, LinkStatus.EXPIRED)
        self.make(invoice)
        self.assertEqual(self.live(invoice).amount, D("750.00"))

    def test_the_bulk_result_screen_does_not_offer_a_stale_link_either(self):
        """Codex on #381: after a desk payment, refreshing the result screen still offered «انسخ اللينك» for the old amount."""

        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.client.post(reverse("education:paylinks_bulk"), {"lang": "ar", "month": f"{self.month:%Y-%m}"}, follow=True)
        result = reverse("education:paylinks_result")
        fresh = self.client.get(result, {"lang": "ar"})
        self.assertContains(fresh, "data-copy-link")
        self.assertNotContains(fresh, "data-pay-stale")
        self.pay_off(invoice, "500", settled=False)  # paid at the desk after the links were made
        stale = self.client.get(result, {"lang": "ar"})
        self.assertContains(stale, "data-pay-stale")
        self.assertContains(stale, "data-pay-cancel")
        self.assertNotContains(stale, "data-copy-link")
        self.assertNotContains(stale, "data-pay-whatsapp")

    def test_an_expired_link_is_not_shown_as_live(self):
        invoice = self.fee_invoice("يوسف محمد", "1250")
        self.make(invoice)
        PaymentLink.objects.filter(invoice=invoice).update(expires_at=timezone.now() - timedelta(minutes=1))
        page = self.client.get(self.fees_url())
        self.assertContains(page, "data-pay-make")
        self.assertNotContains(page, "data-pay-live")

    def test_expiry_in_words(self):
        now = timezone.now()
        words = lambda hours, lang="ar": paylinks.expiry_words(now + timedelta(hours=hours, minutes=-1), lang, now)  # noqa: E731
        self.assertEqual([words(h) for h in (72, 48, 24, 5, 2, 1)], ["بعد 3 أيام", "بعد يومين", "بعد يوم", "بعد 5 ساعات", "بعد ساعتين", "بعد ساعة"])
        self.assertEqual(paylinks.expiry_words(now + timedelta(minutes=20), "ar", now), "بعد أقل من ساعة")
        self.assertEqual([words(h, "en") for h in (72, 24, 5)], ["in 3 days", "in 1 day", "in 5 hours"])
        self.assertEqual(paylinks.expiry_words(now - timedelta(minutes=1), "en", now), "expired")
