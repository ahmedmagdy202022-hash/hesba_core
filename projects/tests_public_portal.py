"""Contracting C10 (HG-135): the project owner's page behind a signed link, and the owner's approval of a variation order.

Every figure is on the reference contract of ``tests_variation_baseline`` (1,000,000.00: A 400 m³ × 1,500.00 and B 2,000 m² ×
200.00; retention 5%, advance recovery 10%, advance 100,000.00 received) and on its fixed clock (Wednesday 11 November 2026):

* certificate 1 (posted): gross 200,000.00, retention 10,000.00, recovery 20,000.00, net 170,000.00;
* certificate 2 (posted): gross 250,000.00, retention 12,500.00, recovery 25,000.00, net 212,500.00;
* certificate 3 (a draft, A to 250 m³): gross 75,000.00, never on the owner's page;
* a cheque of 50,000.00 in hand for a collection: under collection;

so certified to date 450,000.00 (45%), retention held 22,500.00, advance left 55,000.00, under collection 50,000.00 and due
now 332,500.00 (450,000 − 22,500 − 45,000 − 50,000). Deductions to date on certificate 2: retention 22,500.00 and recovery
45,000.00, beside its own 12,500.00 and 25,000.00.

The project also carries what the owner must never see: a subcontract (مؤسسة الأمل للمقاولات, 61,234.00) with a posted bill of
20,111.00, a linked service purchase PI-C10-77 of 3,333.33 and a budget of 777,777.00.
"""

import re
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_supplier
from permissions.models import RoleCode
from printing import share
from purchases.models import PurchaseInvoice
from purchases.services import create_purchase_draft, post_purchase_invoice

from . import contract, costs, public, schedule, services, variations
from .models import CostHeading, ProjectStatus, VariationStatus
from .tests_contract import person
from .tests_documents import view_only
from .tests_variation_baseline import DAY, ReferenceContract, money_footprint

BASE = "https://hesba.example"
NEW_C = {"code": "C", "description": "عزل مائي للسطح", "unit": "م²", "quantity": "1000", "rate": "80"}
BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


def footprint():
    from ledger.projector import ensure_fresh

    ensure_fresh()
    return money_footprint()


def fingerprint_of(html, order):
    found = re.search(r'action="[^"]*/variation/%d/approve/"[^>]*>.*?name="fingerprint" value="([0-9a-f]+)"' % order.pk, html, re.S)
    return found.group(1) if found else None


@override_settings(PUBLIC_BASE_URL=BASE)
class PortalSetup(ReferenceContract):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.first, self.second = self.two_certificates()
        self.draft_certificate = self.certify(post=False, a="250")
        contract.receive_cheque(self.project, self.owner, data={"number": "100245", "bank_name": "بنك مصر", "due_date": (DAY + timedelta(days=30)).isoformat(),
                                                                "amount": "50000", "received_on": DAY.isoformat()})
        self.secrets()
        self.token = share.make_token("project", self.project.pk, owner=self.customer.pk)
        self.url = reverse("share_document", args=[self.token])
        self.anon = Client(enforce_csrf_checks=True)

    def secrets(self):
        supplier = make_supplier(supplier_code="S-SUB", name="مؤسسة الأمل للمقاولات")
        subcontract = costs.save_subcontract(self.project, self.owner, {"supplier": supplier, "scope": "أعمال المحارة", "value": "61234", "retention_rate": "10"})
        bill = costs.bill_subcontract(subcontract, self.owner, amount="20111", description="مستخلص باطن 1")
        post_purchase_invoice(bill.invoice_id, self.owner)
        self.sub_bill_number = PurchaseInvoice.objects.get(pk=bill.invoice_id).invoice_number
        rental = make_item(item_code="RENT", item_name="إيجار ونش", is_stock_tracked=False)
        invoice = create_purchase_draft({"invoice_number": "PI-C10-77", "invoice_date": DAY, "supplier": make_supplier(supplier_code="S-EQ", name="معدات الدلتا"),
                                         "receiving_location": self.store}, [{"item": rental, "quantity": D("1"), "unit_purchase_price": D("3333.33")}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        costs.link_purchase(self.project, PurchaseInvoice.objects.get(pk=invoice.pk), self.owner, heading=CostHeading.EQUIPMENT)
        costs.save_budget(self.project, self.owner, {"subcontract": "777777"})

    def get(self, url=None, **params):
        return self.anon.get(url or self.url, params or None)

    def draft_order(self, **over):
        data = {"order_date": DAY, "title": "تعديلات الدور الأخير", "reason": "طلب المالك في محضر الاجتماع", "extension_days": "0",
                "changes": [(self.a.pk, "20"), (self.b.pk, "-100")], "new_items": [dict(NEW_C)]}
        data.update(over)
        return variations.save_order(self.project, self.owner, **data)

    def approve_url(self, order, token=None):
        return reverse("share_project_variation_approve", args=[token or self.token, order.pk])

    def post_approval(self, order, name="م. أحمد سامي", fingerprint=None, token=None, lang="ar"):
        page = self.anon.get(reverse("share_document", args=[token or self.token]), {"lang": lang})
        if fingerprint is None:
            fingerprint = fingerprint_of(page.content.decode(), order)
        return self.anon.post(self.approve_url(order, token), {"name": name, "fingerprint": fingerprint or "", "lang": lang,
                                                               "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})

    def assertPrivate(self, response):
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def assertExpired(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "data-share-expired", status_code=404)
        self.assertNotContains(response, self.project.name, status_code=404)
        self.assertPrivate(response)


class PageFiguresTests(PortalSetup):
    def test_the_page_shows_the_contract_and_the_account_to_the_piastre(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        figures = dict(re.findall(r'data-figure="(\w+)"><bdi>([\d,.]+)</bdi>', html))
        self.assertEqual(figures, {"original": "1,000,000.00", "variations": "0.00", "revised": "1,000,000.00", "certified": "450,000.00",
                                   "retention_held": "22,500.00", "advance_left": "55,000.00", "under_collection": "50,000.00", "due_now": "332,500.00"})
        self.assertIn("45% من قيمة العقد", html)
        self.assertIn(self.project.name, html)
        self.assertIn(self.customer.name, html)

    def test_only_posted_certificates_each_with_its_net(self):
        html = self.get().content.decode()
        self.assertEqual(re.findall(r'data-certificate="(\d+)"', html), ["1", "2"])
        self.assertIn("170,000.00", html)
        self.assertIn("212,500.00", html)
        self.assertIn(self.first.invoice.invoice_number, html)
        self.assertIn(self.second.invoice.invoice_number, html)

    def test_never_cost_budget_subcontract_profit_purchases_or_a_draft(self):
        for lang in ("ar", "en"):
            with self.subTest(lang=lang):
                html = self.get(lang=lang).content.decode()
                for text in ("مؤسسة الأمل", "أعمال المحارة", "مستخلص باطن", "معدات الدلتا", "إيجار ونش", "PI-C10-77", self.sub_bill_number,
                             "61,234.00", "20,111.00", "18,099.90", "3,333.33", "777,777.00",
                             "تكلفة", "التكلفة", "ميزانية", "الميزانية", "باطن", "ربح", "الربح", "مشتريات", "هامش",
                             "Cost", "cost", "Budget", "budget", "ubcontract", "Profit", "profit", "urchase", "Margin",
                             self.draft_certificate.invoice.invoice_number, "مستخلص رقم 3", "Certificate 3", "75,000.00"):
                    self.assertNotIn(text, html)
                for word in BANNED:
                    self.assertNotIn(word, html)

    def test_the_figures_to_date_are_read_once_per_certificate(self):
        """The page reads each posted certificate once for its running figures, not once per pair (Codex on #442)."""

        posted = list(public._posted(self.project))
        with mock.patch.object(contract, "effective", wraps=contract.effective) as reading:
            running = contract.to_date_running(posted)
        self.assertEqual(reading.call_count, len(posted))
        self.assertEqual(running, {c.pk: contract.to_date(c) for c in posted})
        with mock.patch.object(contract, "to_date", side_effect=AssertionError("the page must use the running figures")):
            self.assertEqual(self.get().status_code, 200)

    def test_the_statement_carries_the_deductions_to_date(self):
        self.assertEqual(contract.to_date(self.first), {"gross": D("200000.00"), "retention": D("10000.00"), "recovery": D("20000.00")})
        self.assertEqual(contract.to_date(self.second), {"gross": D("450000.00"), "retention": D("22500.00"), "recovery": D("45000.00")})
        position = contract.position(self.project)  # the latest posted certificate's "to date" is the project's own reading
        self.assertEqual((position["retention"], position["recovered"]), (D("22500.00"), D("45000.00")))
        for number, expected in ((1, {"gross": ("200,000.00", "200,000.00"), "retention": ("10,000.00", "10,000.00"), "recovery": ("20,000.00", "20,000.00")}),
                                 (2, {"gross": ("250,000.00", "450,000.00"), "retention": ("12,500.00", "22,500.00"), "recovery": ("25,000.00", "45,000.00")})):
            with self.subTest(certificate=number):
                response = self.get(c=str(number))
                self.assertEqual(response.status_code, 200)
                self.assertPrivate(response)
                html = response.content.decode()
                rows = {row: tuple(re.findall(r"<bdi>([\d,.]+)</bdi>", cells))
                        for row, cells in re.findall(r'data-row="(\w+)">(.*?)</div>', html, re.S)}
                self.assertEqual(rows, expected)
                self.assertIn("ضمان الأعمال (5%)", html)
                self.assertIn("لحد المستخلص ده", html)
                self.assertIn("data-print", html)
        statement = self.get(c="2").content.decode()
        self.assertIn('data-statement-net><bdi>212,500.00</bdi>', statement)
        # the lines: A previous 100, this 100, cumulative 200 m³ at 1,500.00; B 250 + 500 = 750 m²
        self.assertIn("300,000.00", statement)
        self.assertIn("150,000.00", statement)
        self.assertIn("100,000.00", statement)

    def test_a_draft_or_unknown_certificate_number_shows_nothing(self):
        for number in ("3", "99", "0", "x1"):
            with self.subTest(number=number):
                self.assertExpired(self.get(c=number))

    def test_the_schedule_progress_when_the_project_has_one(self):
        self.assertNotIn("data-portal-schedule", self.get().content.decode())
        task = schedule.save_task(self.project, self.owner, {"name": "أعمال الهيكل الخرساني", "start_date": (DAY - timedelta(days=100)).isoformat(),
                                                             "end_date": (DAY + timedelta(days=99)).isoformat(), "predecessor": "", "weight": ""})
        schedule.set_progress(self.project, task, self.owner, "40")
        html = self.get().content.decode()
        self.assertIn("data-portal-schedule", html)
        self.assertIn('data-figure="planned"><bdi>50.5%</bdi>', html)  # 101 of 200 days
        self.assertIn('data-figure="actual"><bdi>40%</bdi>', html)
        self.assertIn("أعمال الهيكل الخرساني", html)

    def test_arabic_and_english(self):
        arabic = self.get()
        self.assertContains(arabic, 'lang="ar" dir="rtl"')
        self.assertContains(arabic, "الموقف المالي")
        self.assertContains(arabic, "المستحق دلوقتي")
        self.assertContains(arabic, "English")
        self.assertContains(arabic, f"{self.url}?lang=en")
        english = self.get(lang="en")
        self.assertContains(english, 'lang="en" dir="ltr"')
        self.assertContains(english, "The account")
        self.assertContains(english, "Due now")
        self.assertContains(english, "332,500.00")
        self.assertContains(english, "العربية")
        self.assertNotContains(english, "الموقف المالي")
        statement = self.get(c="2", lang="en")
        self.assertContains(statement, "Retention (5%)")
        self.assertContains(statement, "To date")

    def test_private_headers_on_every_answer(self):
        self.assertPrivate(self.get())
        self.assertPrivate(self.get(lang="en"))
        self.assertContains(self.get(), '<meta name="robots" content="noindex, nofollow">')
        self.assertPrivate(self.get(c="1"))
        self.assertPrivate(self.anon.get(reverse("share_document", args=["forged"])))


class TokenTests(PortalSetup):
    def test_a_forged_or_altered_token_shows_the_expired_page(self):
        self.assertExpired(self.anon.get(reverse("share_document", args=["not-a-token"])))
        altered = self.token[:-2] + ("AA" if not self.token.endswith("AA") else "BB")
        self.assertExpired(self.anon.get(reverse("share_document", args=[altered])))
        other_salt = signing.dumps({"k": "project", "id": self.project.pk, "o": self.customer.pk}, salt="another.salt", compress=True)
        self.assertExpired(self.anon.get(reverse("share_document", args=[other_salt])))

    def test_an_expired_token_shows_the_expired_page(self):
        import time

        later = time.time() + share.max_age() + 60
        with mock.patch("django.core.signing.time.time", return_value=later):
            self.assertExpired(self.get())
        self.assertEqual(self.get().status_code, 200)

    def test_a_link_of_another_customer_is_refused(self):
        stranger = make_customer(customer_code="C-OTHER", name="شركة الأفق")
        self.assertExpired(self.anon.get(reverse("share_document", args=[share.make_token("project", self.project.pk, owner=stranger.pk)])))
        self.assertExpired(self.anon.get(reverse("share_document", args=[share.make_token("project", self.project.pk)])))  # bound to no one

    def test_the_link_dies_when_the_project_changes_hands_or_is_cancelled(self):
        """«Revoked» without a table: a link whose customer is no longer the project's, or of a cancelled project, is dead."""

        from .models import Project

        stranger = make_customer(customer_code="C-NEW", name="شركة الأفق")
        Project.objects.filter(pk=self.project.pk).update(customer=stranger)
        self.assertExpired(self.get())
        Project.objects.filter(pk=self.project.pk).update(customer=self.customer, status=ProjectStatus.CANCELLED)
        self.assertExpired(self.get())
        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.ACTIVE)
        self.assertEqual(self.get().status_code, 200)

    def test_a_token_reaches_only_its_own_project(self):
        other_customer = make_customer(customer_code="C-2", name="شركة الأفق")
        other = services.save_project({"name": "فيلا الأفق", "customer": other_customer, "start_date": DAY}, self.owner)
        other_url = reverse("share_document", args=[share.make_token("project", other.pk, owner=other_customer.pk)])
        html = self.anon.get(other_url).content.decode()
        self.assertIn("فيلا الأفق", html)
        for text in (self.project.name, self.customer.name, "450,000.00", self.first.invoice.invoice_number):
            self.assertNotIn(text, html)
        self.assertExpired(self.anon.get(other_url, {"c": "1"}))  # this project's certificate 1 is not the other's
        # another kind of share token for the same id never opens the project page
        response = self.anon.get(reverse("share_document", args=[share.make_token("work_order", self.project.pk)]))
        self.assertNotContains(response, self.project.name, status_code=response.status_code)

    def test_the_module_switched_off_shows_nothing(self):
        with mock.patch("projects.access.module_open", return_value=False):
            self.assertExpired(self.get())


class ThrottleTests(PortalSetup):
    def test_page_loads_are_throttled_per_address(self):
        with mock.patch.object(public, "PAGE_MINUTE", ("page", 3, 60)):
            for _ in range(3):
                self.assertEqual(self.get().status_code, 200)
            busy = self.get()
            self.assertEqual(busy.status_code, 429)
            self.assertNotContains(busy, self.project.name, status_code=429)
            self.assertPrivate(busy)
            other = Client(REMOTE_ADDR="10.9.9.9")
            self.assertEqual(other.get(self.url).status_code, 200)

    def test_approvals_are_throttled_per_link(self):
        order = self.draft_order()
        with mock.patch.object(public, "APPROVE_TOKEN", ("approve_token", 2, 3600)):
            for _ in range(2):
                self.assertEqual(self.post_approval(order, fingerprint="0" * 32).status_code, 409)
            self.assertEqual(self.post_approval(order).status_code, 429)
        order.refresh_from_db()
        self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_approvals_are_throttled_per_address(self):
        order = self.draft_order()
        with mock.patch.object(public, "APPROVE_ADDRESS", ("approve_ip", 1, 3600)):
            self.assertEqual(self.post_approval(order, fingerprint="0" * 32).status_code, 409)
            self.assertEqual(self.post_approval(order).status_code, 429)


class OwnerApprovalTests(PortalSetup):
    def test_the_waiting_order_is_shown_with_its_lines_and_value(self):
        order = self.draft_order()
        html = self.get().content.decode()
        self.assertIn(f'data-portal-waiting="{order.number}"', html)
        self.assertIn("تعديلات الدور الأخير", html)
        self.assertIn("عزل مائي للسطح", html)
        self.assertIn("+90,000.00", html)
        self.assertEqual(fingerprint_of(html, order), variations.owner_fingerprint(order))

    def test_an_order_changed_after_the_page_was_shown_is_refused(self):
        order = self.draft_order()
        page = self.get().content.decode()
        seen = fingerprint_of(page, order)
        variations.save_order(self.project, self.owner, order=order, order_date=DAY, title="تعديلات الدور الأخير", reason="طلب المالك في محضر الاجتماع",
                              extension_days="0", changes=[(self.a.pk, "25"), (self.b.pk, "-100")], new_items=[dict(NEW_C)])  # +5 m³ more on A
        boq_before, money_before = [(l.code, l.quantity) for l in self.project.boq.all()], footprint()
        response = self.anon.post(self.approve_url(order), {"name": "م. أحمد سامي", "fingerprint": seen, "lang": "ar",
                                                            "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, variations.MESSAGES["ar"]["owner_changed"], status_code=409)
        self.assertPrivate(response)
        order.refresh_from_db()
        self.assertEqual((order.status, order.owner_reference, order.decided_by_id), (VariationStatus.DRAFT, "", None))
        self.assertEqual([(l.code, l.quantity) for l in self.project.boq.all()], boq_before)
        self.assertEqual(footprint(), money_before)
        # the page shows the order as it is now, with its new fingerprint
        self.assertEqual(fingerprint_of(response.content.decode(), order), variations.owner_fingerprint(order))
        self.assertNotEqual(seen, variations.owner_fingerprint(order))

    def test_an_edit_landing_while_the_page_renders_cannot_be_approved_unseen(self):
        """The lines the page shows and the fingerprint it carries come from one reading: an office edit that lands between
        them leaves the page sealing what it showed, so the approval is refused (Codex on #442)."""

        order, real, done = self.draft_order(), variations.owner_fingerprint, []

        def edit_then_seal(order_now, lines=None):
            if not done:  # the office saves +5 m³ more on A after the page read the lines, before it sealed them
                done.append(True)
                variations.save_order(self.project, self.owner, order=order, order_date=DAY, title="تعديلات الدور الأخير",
                                      reason="طلب المالك في محضر الاجتماع", extension_days="0",
                                      changes=[(self.a.pk, "25"), (self.b.pk, "-100")], new_items=[dict(NEW_C)])
            return real(order_now, lines)

        with mock.patch.object(variations, "owner_fingerprint", side_effect=edit_then_seal):
            page = self.get().content.decode()
        self.assertEqual(done, [True])
        seen = fingerprint_of(page, order)
        self.assertNotEqual(seen, variations.owner_fingerprint(order))
        money_before = footprint()
        response = self.anon.post(self.approve_url(order), {"name": "م. أحمد سامي", "fingerprint": seen, "lang": "ar",
                                                            "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 409)
        order.refresh_from_db()
        self.assertEqual((order.status, order.decided_by_id), (VariationStatus.DRAFT, None))
        self.assertEqual(footprint(), money_before)

    def test_approving_the_unchanged_order_records_the_owner_and_moves_no_money(self):
        order = self.draft_order()
        before = footprint()
        response = self.post_approval(order, name="  م. أحمد   سامي  ")
        self.assertEqual(response.status_code, 303)
        self.assertPrivate(response)
        self.assertEqual(response["Location"], f"{self.url}?lang=ar&done={order.pk}")
        order.refresh_from_db()
        actor = get_user_model().objects.get(username=variations.OWNER_LINK_USERNAME)
        self.assertEqual((order.status, order.value, order.approved_on, order.owner_reference), (VariationStatus.APPROVED, D("90000.00"), DAY, "م. أحمد سامي"))
        self.assertEqual((order.decided_by, order.decision_note), (actor, "اعتمده المالك من رابط المشروع: م. أحمد سامي"))
        self.assertEqual((actor.is_active, actor.is_staff, actor.is_superuser, actor.has_usable_password(), actor.first_name),
                         (False, False, False, False, "رابط المالك"))
        self.assertFalse(hasattr(actor, "hesba_profile") and actor.hesba_profile.role_id)
        # exactly C1's approval: the BOQ moves and the contract is revised; no invoice, payment, ledger, stock, cashbox or journal row
        self.assertEqual([(l.code, l.quantity, l.rate, l.variation_id) for l in self.project.boq.all()],
                         [("A", D("420.000"), D("1500.00"), None), ("B", D("1900.000"), D("200.00"), None), ("C", D("1000.000"), D("80.00"), order.pk)])
        self.assertEqual(self.project.boq.get(code="C").created_by, actor)
        figures = variations.figures(self.project)
        self.assertEqual((figures["original"], figures["approved"], figures["revised"], figures["contract"]),
                         (D("1000000.00"), D("90000.00"), D("1090000.00"), D("1090000.00")))
        self.assertEqual(footprint(), before)
        audit = AuditLog.objects.filter(action="approve_variation_order", object_id=str(order.pk)).get()
        self.assertEqual((audit.actor, audit.after_data["via"], audit.after_data["contract_after"]), (actor, "owner_link", "1090000.00"))
        # the page after the redirect: the message, the revised contract and who approved it
        page = self.anon.get(response["Location"])
        self.assertContains(page, "اتسجّل اعتماد حضرتك لأمر التغيير رقم 1.")
        self.assertContains(page, 'data-figure="revised"><bdi>1,090,000.00</bdi>')
        self.assertContains(page, "اعتمده: <bdi>م. أحمد سامي</bdi>")
        self.assertNotContains(page, "data-approve-form")
        # the staff page names the owner's link and the typed name
        self.client.force_login(self.owner)
        staff = self.client.get(reverse("projects:variation", args=[self.project.pk, order.pk]))
        self.assertContains(staff, "رابط المالك")
        self.assertContains(staff, "م. أحمد سامي")

    def test_a_second_approval_of_the_same_order_changes_nothing(self):
        order = self.draft_order()
        self.assertEqual(self.post_approval(order).status_code, 303)
        before = (footprint(), [(l.code, l.quantity) for l in self.project.boq.all()])
        again = self.anon.post(self.approve_url(order), {"name": "شخص تاني", "fingerprint": "0" * 32, "lang": "ar",
                                                         "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertEqual(again.status_code, 409)
        self.assertContains(again, "مبقاش في انتظار الاعتماد", status_code=409)
        order.refresh_from_db()
        self.assertEqual(order.owner_reference, "م. أحمد سامي")
        self.assertEqual((footprint(), [(l.code, l.quantity) for l in self.project.boq.all()]), before)

    def test_a_name_is_required(self):
        order = self.draft_order()
        for name in ("", "   ", "ا" * 81):
            with self.subTest(name=name[:5]):
                response = self.post_approval(order, name=name)
                self.assertEqual(response.status_code, 409)
                order.refresh_from_db()
                self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_an_omission_below_what_is_certified_is_refused_in_owner_words(self):
        order = self.draft_order(changes=[(self.b.pk, "-1200")], new_items=[])  # B to 800 m² is fine when drafted: 750 certified
        from sales.services import post_sales_invoice

        post_sales_invoice(self.draft_certificate.invoice_id, self.owner)
        self.certify(b="900")  # a certificate in between: 900 m² of B now certified
        response = self.post_approval(order)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, public.WORDS["ar"]["cannot"], status_code=409)
        self.assertNotContains(response, "صلاحية", status_code=409)
        order.refresh_from_db()
        self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_a_closed_project_cannot_approve(self):
        order = self.draft_order()
        page = self.get().content.decode()
        seen = fingerprint_of(page, order)
        type(self.project).objects.filter(pk=self.project.pk).update(status=ProjectStatus.DONE)
        self.assertNotIn("data-approve-form", self.get().content.decode())
        response = self.anon.post(self.approve_url(order), {"name": "م. أحمد سامي", "fingerprint": seen, "lang": "ar",
                                                            "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_another_projects_order_or_a_closed_one_is_not_reachable(self):
        other_customer = make_customer(customer_code="C-2", name="شركة الأفق")
        other = services.save_project({"name": "فيلا الأفق", "customer": other_customer, "start_date": DAY}, self.owner)
        contract.save_boq_line(other, self.owner, {"code": "X", "description": "أعمال", "unit": "م²", "quantity": "10", "rate": "100"})
        foreign = variations.save_order(other, self.owner, order_date=DAY, title="أمر مشروع تاني", changes=[], new_items=[dict(NEW_C)])
        rejected = self.draft_order()
        self.get()  # the page with a waiting order sets the CSRF cookie
        response = self.anon.post(self.approve_url(foreign), {"name": "x", "fingerprint": variations.owner_fingerprint(foreign), "lang": "ar",
                                                              "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertExpired(response)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, VariationStatus.DRAFT)
        variations.close(self.project, rejected, self.owner, status=VariationStatus.REJECTED)
        self.assertExpired(self.post_approval(rejected, fingerprint="0" * 32))
        self.assertNotIn("تعديلات الدور الأخير", self.get().content.decode())  # a rejected order is not on the owner's page

    def test_a_forged_token_cannot_approve(self):
        order = self.draft_order()
        self.get()
        response = self.anon.post(self.approve_url(order, token="forged"), {"name": "x", "fingerprint": variations.owner_fingerprint(order),
                                                                            "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value})
        self.assertExpired(response)
        stranger = make_customer(customer_code="C-OTHER", name="شركة الأفق")
        bound_elsewhere = share.make_token("project", self.project.pk, owner=stranger.pk)
        self.assertExpired(self.anon.post(self.approve_url(order, token=bound_elsewhere), {"name": "x", "fingerprint": variations.owner_fingerprint(order),
                                                                                           "csrfmiddlewaretoken": self.anon.cookies["csrftoken"].value}))
        order.refresh_from_db()
        self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_the_approval_needs_csrf_and_a_post(self):
        order = self.draft_order()
        self.assertEqual(self.anon.post(self.approve_url(order), {"name": "x", "fingerprint": variations.owner_fingerprint(order)}).status_code, 403)
        self.assertEqual(self.anon.get(self.approve_url(order)).status_code, 405)
        order.refresh_from_db()
        self.assertEqual(order.status, VariationStatus.DRAFT)

    def test_english_messages(self):
        order = self.draft_order()
        response = self.post_approval(order, name="", lang="en")
        self.assertContains(response, variations.MESSAGES["en"]["owner_name"], status_code=409)
        self.assertEqual(self.post_approval(order, name="Eng. Ahmed Samy", lang="en").status_code, 303)
        self.assertContains(self.anon.get(f"{self.url}?lang=en&done={order.pk}"), "Your approval of variation order 1 is recorded.")

    def test_the_office_approval_is_unchanged(self):
        """The staff path still needs the signed copy and the permission (the refactor kept its guards)."""

        from django.core.exceptions import PermissionDenied, ValidationError

        order = self.draft_order()
        with self.assertRaisesMessage(ValidationError, variations.MESSAGES["ar"]["signed_copy"]):
            variations.approve(self.project, order, self.owner)
        with self.assertRaises(PermissionDenied):
            variations.approve(self.project, order, view_only(self.owner, username="c10_viewer"))


class ShareLinkTests(PortalSetup):
    def make(self, user):
        self.client.force_login(user)
        return self.client.post(reverse("projects:owner_link", args=[self.project.pk]), {"lang": "ar"})

    def test_the_office_makes_the_link_bound_to_the_projects_customer(self):
        response = self.make(self.owner)
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        html = page.content.decode()
        url = re.search(r'data-owner-link-url>([^<]+)<', html).group(1)
        self.assertTrue(url.startswith(f"{BASE}/share/"))
        token = url.split("/share/")[1].split("/")[0]
        self.assertEqual(share.read_token_data(token), {"k": "project", "id": self.project.pk, "o": self.customer.pk})
        self.assertIn("data-copy-link", html)
        self.assertIn("wa.me", html)
        self.assertNotIn("لينك", html[html.index("data-owner-link"):html.index("data-owner-link") + 2000])
        self.assertEqual(self.anon.get(f"/share/{token}/").status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="share_owner_link", object_id=str(self.project.pk), actor=self.owner).exists())
        # shown once: the next visit offers to make a new one
        self.assertNotIn("data-owner-link-url", self.client.get(reverse("projects:detail", args=[self.project.pk])).content.decode())

    def test_who_may_make_it(self):
        viewer = view_only(self.owner, username="c10_viewer")  # sees the contract and certificates, approves nothing
        engineer = person(RoleCode.SITE_ENGINEER, "c10_engineer")
        for user in (viewer, engineer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                detail = self.client.get(reverse("projects:detail", args=[self.project.pk]))
                self.assertNotContains(detail, "data-owner-link")
                self.assertEqual(self.client.post(reverse("projects:owner_link", args=[self.project.pk]), {"lang": "ar"}).status_code, 403)
        self.assertFalse(public.can_share(viewer))
        self.assertTrue(public.can_share(self.owner))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("projects:owner_link", args=[self.project.pk])).status_code, 405)

    @override_settings(PUBLIC_BASE_URL="")
    def test_no_link_without_the_public_address(self):
        response = self.make(self.owner)
        page = self.client.get(response["Location"])
        self.assertContains(page, public.WORDS["ar"]["no_base_url"])
        self.assertNotContains(page, "data-owner-link-url")

    def test_no_link_for_a_cancelled_project(self):
        type(self.project).objects.filter(pk=self.project.pk).update(status=ProjectStatus.CANCELLED)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("projects:detail", args=[self.project.pk])), "data-owner-link")
        page = self.client.get(self.make(self.owner)["Location"])
        self.assertNotContains(page, "data-owner-link-url")
