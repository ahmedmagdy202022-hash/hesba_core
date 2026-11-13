"""S2: the customer's secure link to a work order: see its status, approve or refuse the quote (backend only)."""

import time
from decimal import Decimal as D
from unittest.mock import patch

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.test import Client, override_settings
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_customer
from inventory.models import StockMovement
from inventory.services import get_item_stock_quantity
from ledger.models import JournalEntry, JournalLine
from parties.services import balance
from permissions.models import RoleCode
from printing.share import SALT, make_token, public_url, read_token
from sales.models import CustomerLedgerEntry, SalesInvoice

from . import services
from .models import Status, WorkOrder, WorkOrderEvent
from .tests import WorkshopSetup, person

BASE = "https://shop.example.com"
SECRET_NOTE = "ملاحظة-داخلية-سرية"


class CustomerLinkTests(WorkshopSetup):
    def setUp(self):
        super().setUp()
        self.order_ = self.order(notes=SECRET_NOTE)
        services.add_line(self.order_, self.part, self.owner, quantity="1")
        services.add_line(self.order_, self.labour, self.owner)
        for status in (Status.DIAGNOSED, Status.QUOTED):
            services.move(self.order_, status, self.owner)
        self.order_.refresh_from_db()
        self.public = Client()

    # -- helpers --

    def token(self, order=None):
        return make_token("work_order", (order or self.order_).pk)

    def page(self, order=None, lang="ar", client=None):
        return (client or self.public).get(reverse("share_document", args=[self.token(order)]), {"lang": lang})

    def decide(self, action, order=None, lang="ar", client=None, fingerprint=None):
        """POST as the page would: echoing the fingerprint of the quote it showed (current unless given)."""

        order = order or self.order_
        fingerprint = services.quote_fingerprint(order) if fingerprint is None else fingerprint
        return (client or self.public).post(reverse(f"share_work_order_{action}", args=[self.token(order)]), {"lang": lang, "fingerprint": fingerprint})

    def money(self):
        return {
            "rows": [model.objects.count() for model in (SalesInvoice, CashboxMovement, StockMovement, JournalEntry, JournalLine, CustomerLedgerEntry)],
            "stock": get_item_stock_quantity(self.part), "cash": get_cashbox_balance(self.cashbox), "customer": balance("customer", self.customer),
            "lines": [(line.pk, line.quantity, line.unit_price) for line in self.order_.lines.all()],
        }

    def status(self):
        return WorkOrder.objects.get(pk=self.order_.pk).status

    # -- the token --

    def test_work_order_is_a_share_kind_and_other_kinds_or_orders_do_not_pass(self):
        self.assertEqual(read_token(self.token()), ("work_order", self.order_.pk))
        other = self.order()
        self.assertEqual(read_token(self.token(other)), ("work_order", other.pk))
        # The wrong kind of token never reaches the order, on the page or on the decision endpoints.
        wrong = make_token("sales_invoice", self.order_.pk)
        self.assertEqual(self.public.get(reverse("share_document", args=[wrong])).status_code, 404)  # no such invoice
        for action in ("approve", "refuse"):
            response = self.public.post(reverse(f"share_work_order_{action}", args=[wrong]))
            self.assertEqual(response.status_code, 404)
            self.assertContains(response, "data-share-expired", status_code=404)
        self.assertEqual(self.status(), Status.QUOTED)

    def test_a_token_opens_only_its_own_order(self):
        other = self.order(device="OTHER-DEVICE-XYZ")
        body = self.page(other).content.decode()
        self.assertIn("OTHER-DEVICE-XYZ", body)
        self.assertNotIn("OTHER-DEVICE-XYZ", self.page().content.decode())
        services.move(other, Status.DIAGNOSED, self.owner)
        services.move(other, Status.QUOTED, self.owner)
        self.decide("approve", other)
        self.assertEqual((self.status(), WorkOrder.objects.get(pk=other.pk).status), (Status.QUOTED, Status.APPROVED))

    def test_forged_altered_and_expired_tokens_get_the_expired_page(self):
        token = self.token()
        forged = signing.dumps({"k": "work_order", "id": self.order_.pk}, salt="other-salt", compress=True)
        bad_kind = signing.dumps({"k": "purchase_invoice", "id": self.order_.pk}, salt=SALT)
        for bad in (token[:-2] + "xx", forged, bad_kind, "nonsense"):
            with self.subTest(bad=bad[:16]):
                get = self.public.get(reverse("share_document", args=[bad]))
                self.assertContains(get, "data-share-expired", status_code=404)
                self.assertNotContains(get, self.order_.number, status_code=404)
                post = self.public.post(reverse("share_work_order_approve", args=[bad]))
                self.assertContains(post, "data-share-expired", status_code=404)
        self.assertEqual(self.status(), Status.QUOTED)
        with override_settings(SHARE_LINK_DAYS=0), patch("django.core.signing.time.time", return_value=time.time() + 5):
            self.assertContains(self.public.get(reverse("share_document", args=[token])), "data-share-expired", status_code=404)
            self.assertContains(self.public.post(reverse("share_work_order_approve", args=[token])), "data-share-expired", status_code=404)
        self.assertEqual(self.status(), Status.QUOTED)

    # -- GET --

    def test_the_page_shows_status_timeline_quote_and_total_but_no_cost_notes_or_pay(self):
        other_customer = make_customer(customer_code="CUS-OTHER", name="عميل تاني خالص", phone="01110000000")
        self.order(customer=other_customer, device="OTHER-DEVICE-XYZ")
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", response["Cache-Control"])
        data = response.json()
        self.assertEqual((data["number"], data["device"], data["status"], data["can_decide"]), (self.order_.number, "غسالة توشيبا", "quoted", True))
        self.assertEqual(data["shop"], "Demo Store")
        self.assertEqual(data["status_label"], "في انتظار موافقة حضرتك")
        self.assertEqual([step["status"] for step in data["timeline"]], ["received", "diagnosed", "quoted"])
        self.assertEqual([(line["description"], line["amount"]) for line in data["lines"]], [("موتور غسالة", "1800.00"), ("مصنعية إصلاح", "300.00")])
        self.assertEqual(data["total"], "2100.00")
        body = response.content.decode()
        for hidden in (SECRET_NOTE, "1350", "average_cost", "cost", "أحمد سامي", "commission", "OTHER-DEVICE-XYZ", "عميل تاني خالص", "محمد علي", "01001234567", "wo_owner"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, body)

    def test_english_page(self):
        data = self.page(lang="en").json()
        self.assertEqual(data["status_label"], "Waiting for approval")
        self.assertEqual(data["timeline"][0]["label"], "Received")

    # -- POST: approve / refuse --

    def test_approve_moves_quoted_to_approved_once_and_a_second_approve_is_a_no_op(self):
        first = self.decide("approve")
        self.assertEqual(first.status_code, 200)
        self.assertEqual((first.json()["ok"], first.json()["changed"], first.json()["status"]), (True, True, "approved"))
        self.assertEqual(first.json()["message"], "شكرًا لحضرتك، تم تسجيل موافقتك.")
        events, audits = WorkOrderEvent.objects.count(), AuditLog.objects.count()
        second = self.decide("approve")
        self.assertEqual(second.status_code, 200)
        self.assertEqual((second.json()["ok"], second.json()["changed"], second.json()["status"]), (True, False, "approved"))
        self.assertEqual(second.json()["message"], "تم تسجيل رد حضرتك من قبل.")
        self.assertEqual((WorkOrderEvent.objects.count(), AuditLog.objects.count()), (events, audits))  # nothing written the second time
        self.assertEqual(self.status(), Status.APPROVED)

    def test_after_approval_refuse_is_not_allowed(self):
        self.decide("approve")
        response = self.decide("refuse")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((response.json()["ok"], response.json()["status"]), (False, "approved"))
        self.assertEqual(response.json()["message"], "العرض ده مبقاش محتاج رد من حضرتك.")
        self.assertEqual(self.status(), Status.APPROVED)
        self.assertTrue(self.order_.lines.exists())

    def test_refuse_works_from_quoted_only_and_takes_the_quote_off_like_a_staff_refusal(self):
        response = self.decide("refuse", lang="en")
        self.assertEqual((response.status_code, response.json()["status"], response.json()["message"]), (200, "refused", "Done, your refusal is recorded."))
        self.assertEqual((self.status(), self.order_.lines.count()), (Status.REFUSED, 0))
        self.assertEqual(response.json()["lines"], [])
        # A repeated refuse is a no-op; approving a refused quote is not allowed.
        self.assertEqual(self.decide("refuse").json()["changed"], False)
        denied = self.decide("approve", lang="en")
        self.assertEqual((denied.status_code, denied.json()["message"]), (409, "This quote is no longer waiting for your answer."))
        self.assertEqual(self.status(), Status.REFUSED)
        # The customer-facing timeline lists "refused" once, not twice, and no internal note.
        self.assertEqual([step["status"] for step in self.page().json()["timeline"]], ["received", "diagnosed", "quoted", "refused"])
        self.assertNotIn("inspection fee", self.page(lang="en").content.decode())

    def test_neither_answer_is_allowed_from_other_statuses(self):
        for status in (Status.RECEIVED, Status.DIAGNOSED, Status.IN_REPAIR, Status.READY, Status.DELIVERED, Status.CANCELLED):
            for action in ("approve", "refuse"):
                order = self.order()
                WorkOrder.objects.filter(pk=order.pk).update(status=status)
                with self.subTest(status=status, action=action):
                    response = self.decide(action, order)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(WorkOrder.objects.get(pk=order.pk).status, status)
                    self.assertFalse(order.events.filter(by_customer=True).exists())

    def test_the_service_itself_refuses_a_bad_decision_and_an_unquoted_order(self):
        with self.assertRaises(services.ValidationError):
            services.customer_decide(self.order_, "cancel")
        fresh = self.order()
        with self.assertRaises(services.ValidationError):
            services.customer_decide(fresh, "approve")
        self.assertEqual(self.status(), Status.QUOTED)

    def test_get_is_not_a_decision_and_the_endpoints_take_post_only(self):
        self.assertEqual(self.public.get(reverse("share_work_order_approve", args=[self.token()])).status_code, 405)
        self.page()
        self.assertEqual(self.status(), Status.QUOTED)

    # -- CSRF --

    def test_csrf_is_enforced_and_an_anonymous_visitor_gets_the_cookie(self):
        strict = Client(enforce_csrf_checks=True)
        url = reverse("share_work_order_approve", args=[self.token()])
        self.assertEqual(strict.post(url).status_code, 403)
        self.assertEqual(strict.post(url, {"csrfmiddlewaretoken": "wrong"}).status_code, 403)
        self.assertEqual(self.status(), Status.QUOTED)
        opened = self.page(client=strict)
        self.assertIn("csrftoken", opened.cookies)
        token = opened.json()["csrf_token"]
        self.assertEqual(strict.post(url, {"csrfmiddlewaretoken": token, "fingerprint": opened.json()["fingerprint"]}).status_code, 200)
        self.assertEqual(self.status(), Status.APPROVED)

    def test_csrf_header_also_works_and_refuse_is_protected_too(self):
        strict = Client(enforce_csrf_checks=True)
        refuse = reverse("share_work_order_refuse", args=[self.token()])
        self.assertEqual(strict.post(refuse).status_code, 403)
        self.assertEqual(self.status(), Status.QUOTED)
        opened = self.page(client=strict).json()
        self.assertEqual(strict.post(refuse, {"fingerprint": opened["fingerprint"]}, HTTP_X_CSRFTOKEN=opened["csrf_token"]).status_code, 200)
        self.assertEqual(self.status(), Status.REFUSED)

    # -- event and audit --

    def test_event_marks_the_customer_and_the_audit_row_holds_the_order_id_only(self):
        before_audit = set(AuditLog.objects.values_list("pk", flat=True))
        self.decide("approve")
        event = self.order_.events.latest("pk")
        self.assertEqual(event.status, Status.APPROVED)
        self.assertTrue(services.by_customer(event))
        self.assertEqual((event.user, event.by_customer), (None, True))  # no fake actor (HG-073)
        self.assertEqual([step["by_customer"] for step in self.page().json()["timeline"]], [False, False, False, True])
        audit = AuditLog.objects.exclude(pk__in=before_audit).get()
        self.assertEqual((audit.module, audit.action, audit.object_type, audit.object_id, audit.actor), ("workorders", "customer_approve", "workorders.WorkOrder", str(self.order_.pk), None))
        self.assertEqual((audit.before_data, audit.after_data), ({"status": "quoted"}, {"status": "approved", "via": "customer_link"}))
        self.assertEqual((audit.ip_address, audit.user_agent, audit.reason), (None, "", ""))
        blob = repr((audit.before_data, audit.after_data, audit.reason, audit.object_id))
        for personal in ("محمد علي", "01001234567", self.order_.number, self.order_.device):
            self.assertNotIn(personal, blob)

    def test_the_staff_timeline_names_the_customer_and_staff_events_are_unchanged(self):
        staff_before = list(self.order_.events.values_list("pk", "user_id", "by_customer", "note"))
        self.assertTrue(all(user == self.owner.pk and not flag for _, user, flag, _ in staff_before))
        self.decide("approve")
        self.assertEqual(list(self.order_.events.values_list("pk", "user_id", "by_customer", "note"))[:len(staff_before)], staff_before)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("workorders:detail", args=[self.order_.pk]))
        self.assertContains(page, "العميل من اللينك")
        self.assertContains(page, "العميل وافق على العرض من اللينك")
        english = self.client.get(reverse("workorders:detail", args=[self.order_.pk]), {"lang": "en"})
        self.assertContains(english, "Customer (via link)")
        self.assertEqual(english.content.decode().count("Customer (via link)"), 1)  # only the customer's event

    def test_refuse_writes_the_event_and_audit_too(self):
        before_audit = set(AuditLog.objects.values_list("pk", flat=True))
        self.decide("refuse")
        refused = list(self.order_.events.filter(status=Status.REFUSED))
        self.assertTrue(refused and all((event.user, event.by_customer) == (None, True) for event in refused))  # incl. the quote-dropped entry
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("workorders:detail", args=[self.order_.pk])).status_code, 200)  # a null user renders
        actions = sorted(AuditLog.objects.exclude(pk__in=before_audit).values_list("action", flat=True))
        self.assertEqual(actions, ["customer_refuse", "drop_quote"])
        self.assertTrue(all(row.actor is None for row in AuditLog.objects.exclude(pk__in=before_audit)))

    # -- no money --

    def test_no_invoice_cash_stock_or_ledger_row_is_written(self):
        before = self.money()
        self.decide("approve")
        self.decide("approve")
        self.assertEqual(self.money(), before)
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.assertIsNone(WorkOrder.objects.get(pk=self.order_.pk).invoice)

    def test_a_refusal_changes_no_money_either(self):
        before = self.money()
        self.decide("refuse")
        after = self.money()
        before.pop("lines"), after.pop("lines")  # a refused quote's lines come off, as for staff; that is not money
        self.assertEqual(after, before)

    # -- the staff side --

    @override_settings(PUBLIC_BASE_URL=BASE)
    def test_the_link_uses_public_base_url_not_the_request_host(self):
        url = services.customer_link(self.order_, "en")
        self.assertTrue(url.startswith(f"{BASE}/share/"), url)
        self.assertTrue(url.endswith("?lang=en"))
        with patch("django.core.signing.time.time", return_value=time.time()):  # a token is signed with its second: both made in one
            self.assertEqual(public_url("work_order", self.order_.pk), services.customer_link(self.order_))
        token = url.split("/share/")[1].split("/")[0]
        self.assertEqual(read_token(token), ("work_order", self.order_.pk))
        self.client.force_login(self.owner)
        with override_settings(ALLOWED_HOSTS=["*"]):
            response = self.client.get(reverse("workorders:customer_link", args=[self.order_.pk]), HTTP_HOST="evil.example.org")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["url"].startswith(f"{BASE}/share/"))
        self.assertNotIn("evil.example.org", response.json()["url"])
        # The link it hands out opens the order for the customer.
        path = response.json()["url"][len(BASE):]
        self.assertEqual(self.public.get(path).json()["number"], self.order_.number)

    @override_settings(PUBLIC_BASE_URL="")
    def test_without_a_public_base_url_there_is_no_link_and_a_message_says_why(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("workorders:customer_link", args=[self.order_.pk]), {"lang": "en"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"ok": False, "message": "The site address (PUBLIC_BASE_URL) is not set; set it first so the link can be sent."})
        with self.assertRaises(services.ValidationError):
            services.customer_link(self.order_)

    @override_settings(PUBLIC_BASE_URL=BASE)
    def test_only_counter_staff_get_the_link_because_it_can_approve_as_the_customer(self):
        from hesba_testing.factories import grant, make_role, make_user, make_user_profile
        from permissions.models import Permission
        from permissions.services import user_has_permission

        url = reverse("workorders:customer_link", args=[self.order_.pk])
        self.assertEqual(self.public.get(url).status_code, 302)  # anonymous: sent to sign in
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "wo_stock_link"))
        self.assertEqual(self.client.get(url).status_code, 403)  # cannot see work orders
        # A technician on the order: may see it, may not move a quote, so may not hold its approval link.
        technician = make_user(username="wo_tech_link")
        make_user_profile(user=technician, role=make_role("ROLE-TECH-LINK"))
        self.tech.user = technician
        self.tech.save()
        WorkOrder.objects.filter(pk=self.order_.pk).update(technician=self.tech)
        self.client.force_login(technician)
        self.assertEqual(self.client.get(reverse("workorders:detail", args=[self.order_.pk])).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 403)
        # A user who can view orders but not manage them (the same rule that stops them approving a quote).
        viewer = make_user(username="wo_viewer_link")
        role = make_role("ROLE-VIEW-LINK")
        grant(role, Permission.objects.get(code=services.VIEW))
        make_user_profile(user=viewer, role=role)
        self.assertTrue(services.can_view(viewer) and not services.can_manage(viewer))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        # Counter staff: whoever the staff rule lets approve a quote (cashier and manager hold sales.create_sales_invoice).
        for role_code, name in ((RoleCode.CASHIER, "wo_cash_link"), (RoleCode.MANAGER, "wo_mgr_link")):
            counter = person(role_code, name)
            self.assertTrue(user_has_permission(counter, services.MANAGE), role_code)
            self.client.force_login(counter)
            self.assertEqual(self.client.get(url).status_code, 200, role_code)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("workorders:customer_link", args=[999999])).status_code, 404)
        self.assertEqual(self.client.get(url, {"lang": "en"}).json()["url"].rsplit("=", 1)[1], "en")
        with self.assertRaises(PermissionDenied):
            services.customer_link(self.order_, user=technician)
        with override_settings(PUBLIC_BASE_URL=BASE):
            self.assertTrue(services.customer_link(self.order_))  # server code (S7) passes no user

    # -- the quote the customer saw --

    def test_the_page_carries_a_fingerprint_that_follows_the_quote(self):
        first = self.page().json()["fingerprint"]
        self.assertEqual(first, self.page().json()["fingerprint"])
        services.add_line(self.order_, self.labour, self.owner)
        self.assertNotEqual(first, self.page().json()["fingerprint"])

    def test_a_quote_changed_after_the_page_was_read_is_refused_and_the_status_stays(self):
        seen = self.page().json()["fingerprint"]
        services.add_line(self.order_, self.labour, self.owner, quantity="2")  # staff edit the quote meanwhile
        events, audits = WorkOrderEvent.objects.count(), AuditLog.objects.count()
        for action, lang, message in (("approve", "ar", "العرض اتغيّر، افتح الرابط تاني."), ("refuse", "en", "The quote changed; open the link again.")):
            response = self.decide(action, lang=lang, fingerprint=seen)
            self.assertEqual((response.status_code, response.json()["message"]), (409, message))
            self.assertNotEqual(response.json()["fingerprint"], seen)  # the new one, for the reopened page
            self.assertEqual(self.status(), Status.QUOTED)
        self.assertEqual((WorkOrderEvent.objects.count(), AuditLog.objects.count()), (events, audits))
        self.assertEqual(self.order_.lines.count(), 3)
        self.assertEqual(self.decide("approve", fingerprint=response.json()["fingerprint"]).json()["status"], "approved")  # reopened: fine

    def test_a_missing_or_wrong_fingerprint_is_refused_and_a_price_change_alone_changes_it(self):
        for bad in ("", "0" * 32):
            response = (self.public.post(reverse("share_work_order_approve", args=[self.token()]), {"fingerprint": bad}))
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.public.post(reverse("share_work_order_approve", args=[self.token()])).status_code, 409)
        self.assertEqual(self.status(), Status.QUOTED)
        seen = self.page().json()["fingerprint"]
        line = self.order_.lines.first()
        line.unit_price = D("1700.00")
        line.save()
        self.assertEqual(self.decide("approve", fingerprint=seen).status_code, 409)
        self.assertEqual(self.status(), Status.QUOTED)

    def test_the_fingerprint_matches_the_lines_shown_even_if_the_quote_changes_mid_request(self):
        real_totals, fresh = services.totals, services.quote_fingerprint(self.order_)

        def totals_then_staff_edit(order):
            snapshot = real_totals(order)
            services.add_line(self.order_, self.labour, self.owner, quantity="2")  # staff edit right after the read
            return snapshot

        with patch.object(services, "totals", side_effect=totals_then_staff_edit):
            body = self.page().json()
        self.assertEqual(len(body["lines"]), 2)  # the old lines were shown ...
        self.assertEqual(body["fingerprint"], fresh)  # ... and the fingerprint is theirs, not the new quote's
        self.assertEqual(self.decide("approve", fingerprint=body["fingerprint"]).status_code, 409)  # so approving is refused
        self.assertEqual(self.status(), Status.QUOTED)

    def test_a_description_only_change_changes_the_fingerprint_and_the_answer_is_refused(self):
        seen = self.page().json()["fingerprint"]
        line = self.order_.lines.first()
        line.description = "قطعة تانية خالص"
        line.save()
        self.assertNotEqual(self.page().json()["fingerprint"], seen)
        response = self.decide("approve", fingerprint=seen)
        self.assertEqual((response.status_code, response.json()["message"]), (409, "العرض اتغيّر، افتح الرابط تاني."))
        self.assertEqual(self.status(), Status.QUOTED)

    def test_the_same_fingerprint_approves(self):
        seen = self.page().json()["fingerprint"]
        response = self.decide("approve", fingerprint=seen)
        self.assertEqual((response.status_code, response.json()["changed"], self.status()), (200, True, Status.APPROVED))

    # -- a repeat is a no-op only when the customer gave the first answer --

    def test_a_repeat_after_staff_decided_is_not_reported_as_already_recorded(self):
        services.move(self.order_, Status.APPROVED, self.owner)  # the counter approved it
        response = self.decide("approve")
        self.assertEqual((response.status_code, response.json()["ok"]), (409, False))
        self.assertEqual(response.json()["message"], "العرض ده مبقاش محتاج رد من حضرتك.")
        self.assertNotEqual(response.json().get("message"), "تم تسجيل رد حضرتك من قبل.")
        self.assertFalse(self.order_.events.filter(by_customer=True).exists())
        # Same for a quote staff refused.
        other = self.order()
        for status in (Status.DIAGNOSED, Status.QUOTED, Status.REFUSED):
            services.move(other, status, self.owner)
        self.assertEqual(self.decide("refuse", other).status_code, 409)
