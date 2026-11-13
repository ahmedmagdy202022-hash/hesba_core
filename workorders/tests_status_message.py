"""S7: the customer is messaged when their work order is quoted, ready or delivered (through the T9 outbox)."""

import os
from decimal import Decimal as D
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from cashboxes.models import CashboxMovement
from cashboxes.services import get_cashbox_balance
from inventory.models import StockMovement
from inventory.services import get_item_stock_quantity
from ledger.models import JournalEntry, JournalLine
from messaging import services as messaging
from messaging import whatsapp
from messaging.models import MessageKind, MessageTemplate, OutboxMessage, OutboxStatus
from messaging.tests_whatsapp import ENV, FakeGraph
from parties.services import balance
from sales.models import CustomerLedgerEntry, SalesInvoice
from settings_core.models import ClientProfile

from . import services
from .models import Status, WorkOrder
from .tests import WorkshopSetup

BASE = "https://shop.example.com"


@override_settings(PUBLIC_BASE_URL=BASE)
class StatusMessageTests(WorkshopSetup):
    def setUp(self):
        super().setUp()
        self.order_ = self.order()
        services.add_line(self.order_, self.part, self.owner, quantity="1")
        services.add_line(self.order_, self.labour, self.owner)

    # -- helpers --

    def go(self, *statuses, order=None):
        order = order or self.order_
        for status in statuses:
            with self.captureOnCommitCallbacks(execute=True):
                services.move(order, status, self.owner)
        order.refresh_from_db()
        return order

    def deliver(self, order=None):
        order = order or self.order_
        with self.captureOnCommitCallbacks(execute=True):
            services.deliver(order, self.owner, paid_now="0")
        order.refresh_from_db()
        return order

    def messages(self, order=None):
        return OutboxMessage.objects.filter(kind=MessageKind.WORKORDER, key__startswith=f"workorder:{(order or self.order_).pk}:")

    def keys(self, order=None):
        """The keys of the messages that can still go out (a withdrawn one is kept only as the once-per-status record)."""
        return sorted(self.messages(order).exclude(status=OutboxStatus.WITHDRAWN).values_list("key", flat=True))

    def one(self, status, order=None):
        return self.messages(order).get(key=f"workorder:{(order or self.order_).pk}:{status}")

    def money(self):
        return {
            "rows": [model.objects.count() for model in (SalesInvoice, CashboxMovement, StockMovement, JournalEntry, JournalLine, CustomerLedgerEntry)],
            "stock": get_item_stock_quantity(self.part), "cash": get_cashbox_balance(self.cashbox), "customer": balance("customer", self.customer),
            "lines": [(line.pk, line.quantity, line.unit_price) for line in self.order_.lines.all()],
        }

    # -- once per status --

    def test_one_message_per_status_and_a_repeat_never_duplicates(self):
        pk = self.order_.pk
        self.go(Status.DIAGNOSED)
        self.assertEqual(self.keys(), [])  # diagnosed is not a key status
        self.go(Status.QUOTED)
        self.assertEqual(self.keys(), [f"workorder:{pk}:quoted"])
        self.go(Status.APPROVED, Status.IN_REPAIR)
        self.assertEqual(len(self.keys()), 1)
        self.go(Status.READY)
        self.assertEqual(self.keys(), [f"workorder:{pk}:quoted", f"workorder:{pk}:ready"])
        # Back to the bench and ready again, even on a later day: no second "ready".
        self.go(Status.IN_REPAIR, Status.READY)
        OutboxMessage.objects.update(day=OutboxMessage.objects.first().day.replace(year=2020))
        self.go(Status.IN_REPAIR, Status.READY)
        self.assertEqual(self.messages().filter(key__endswith=":ready").count(), 1)
        self.deliver()
        self.assertEqual(self.keys(), [f"workorder:{pk}:delivered", f"workorder:{pk}:quoted", f"workorder:{pk}:ready"])
        # Asking again for any of them (a repeated call) adds nothing, even for a message already sent or skipped.
        OutboxMessage.objects.filter(key=f"workorder:{pk}:ready").update(status=OutboxStatus.SKIPPED)
        order = WorkOrder.objects.get(pk=pk)
        for status in ("quoted", "ready", "delivered"):
            self.assertIsNone(messaging.queue_workorder(order, status, link=BASE))
        self.assertEqual(self.messages().count(), 3)
        self.assertEqual(set(self.messages().values_list("kind", flat=True)), {"workorder"})

    def test_an_order_reaching_a_status_by_the_second_order_has_its_own_message(self):
        other = self.order()
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.go(Status.DIAGNOSED, Status.QUOTED, order=other)
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.WORKORDER).count(), 2)

    # -- the text --

    def test_quoted_message_has_the_shop_the_number_and_the_public_link(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        message = self.one("quoted")
        link = self.link_in(message.text)
        self.assertTrue(message.text.endswith(link))
        self.assertIn(self.order_.number, message.text)
        self.assertIn("Demo Store", message.text)
        self.assertIn(self.customer.name, message.text)
        self.assertIn("عرض السعر جاهز", message.text)
        self.assertEqual((message.phone, message.status, message.link), ("201001234567", OutboxStatus.PENDING, f"/workorders/{self.order_.pk}/"))

    def test_ready_message_carries_the_link_too(self):
        self.go(Status.IN_REPAIR, Status.READY)
        message = self.one("ready")
        self.link_in(message.text)
        self.assertIn("جاهز للاستلام", message.text)

    def test_delivered_message_has_no_link_and_the_warranty_days(self):
        self.go(Status.DIAGNOSED, Status.APPROVED, Status.IN_REPAIR, Status.READY)
        self.deliver()
        text = self.one("delivered").text
        self.assertNotIn(BASE, text)
        self.assertNotIn("http", text)
        self.assertIn("30", text)
        self.assertIn("الضمان 30 يوم", text)
        self.assertIn(self.order_.number, text)
        self.assertIn("Demo Store", text)

    def test_delivered_without_warranty_days_says_no_warranty_line(self):
        order = self.order(warranty_days="0")
        self.go(Status.IN_REPAIR, Status.READY, order=order)
        self.deliver(order)
        text = self.one("delivered", order).text
        self.assertNotIn("الضمان", text)
        self.assertNotIn("http", text)

    def test_english_defaults_when_the_business_language_is_english(self):
        ClientProfile.objects.update(default_language="en")
        self.go(Status.DIAGNOSED, Status.QUOTED, Status.APPROVED, Status.IN_REPAIR, Status.READY)
        self.deliver()
        quoted, ready, delivered = self.one("quoted").text, self.one("ready").text, self.one("delivered").text
        self.assertTrue(quoted.startswith(f"Hello {self.customer.name}, from Demo Store: work order {self.order_.number} the quote is ready"))
        self.assertIn("lang=en", quoted)
        self.assertIn(BASE, quoted)
        self.assertIn("is ready for pickup", ready)
        self.assertIn("lang=en", ready)
        self.assertIn("was delivered", delivered)
        self.assertIn("warranty is 30 days", delivered)
        self.assertNotIn("http", delivered)

    def test_arabic_link_language_by_default(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertIn("lang=ar", self.one("quoted").text)

    def test_the_shops_edited_wording_is_used(self):
        messaging.save_template(MessageKind.WORKORDER, {"text": "{company} - {number} - {status} - {link} - {name}", "active": "1", "every_days": "1"}, self.owner)
        self.go(Status.DIAGNOSED, Status.QUOTED)
        text = self.one("quoted").text
        self.assertTrue(text.startswith(f"Demo Store - {self.order_.number} - عرض السعر جاهز"))
        self.assertTrue(text.endswith(f" - {self.customer.name}"))

    def test_the_default_wording_can_be_saved_unchanged_and_lists_in_the_outbox_templates(self):
        row = messaging.template(MessageKind.WORKORDER)
        self.assertIn("{status}", row.text)
        self.assertIn("{link}", row.text)
        self.assertIn(MessageKind.WORKORDER, [item.kind for item in messaging.templates()])
        saved = messaging.save_template(MessageKind.WORKORDER, {"text": row.text, "active": "1", "every_days": "1"}, self.owner)
        self.assertEqual(saved.text, row.text)
        self.assertIn("workorder", messaging.KIND_LABELS["ar"])
        self.assertIn("workorder", messaging.KIND_LABELS["en"])

    # -- the link --

    @override_settings(PUBLIC_BASE_URL="")
    def test_without_a_public_base_url_the_message_is_queued_without_a_link_and_a_warning_is_logged(self):
        with self.assertLogs("workorders.services", level="WARNING") as logs:
            self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertTrue(any("PUBLIC_BASE_URL" in line for line in logs.output))
        text = self.one("quoted").text
        self.assertNotIn("http", text)
        self.assertIn(self.order_.number, text)
        self.assertIn("عرض السعر جاهز", text)
        self.assertEqual(self.order_.status, Status.QUOTED)

    # -- phone, switch --

    def test_no_phone_means_no_message(self):
        self.customer.phone = ""
        self.customer.whatsapp = ""
        self.customer.save()
        self.go(Status.DIAGNOSED, Status.QUOTED, Status.APPROVED, Status.IN_REPAIR, Status.READY)
        self.deliver()
        self.assertEqual(OutboxMessage.objects.exclude(status=OutboxStatus.WITHDRAWN).count(), 0)

    def test_the_whatsapp_number_is_preferred_over_the_phone(self):
        self.customer.whatsapp = "01112223334"
        self.customer.save()
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertEqual(self.one("quoted").phone, "201112223334")

    def test_a_kind_switched_off_queues_nothing_and_withdraws_what_waits(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertEqual(len(self.keys()), 1)
        messaging.save_template(MessageKind.WORKORDER, {"text": messaging.template(MessageKind.WORKORDER).text, "active": "", "every_days": "1"}, self.owner)
        self.assertFalse(MessageTemplate.objects.get(kind=MessageKind.WORKORDER).active)
        messaging.build()
        self.assertEqual(len(self.keys()), 0)  # switched off: nothing of this kind is left to send
        self.go(Status.APPROVED, Status.IN_REPAIR, Status.READY)
        self.deliver()
        self.assertEqual(OutboxMessage.objects.exclude(status=OutboxStatus.WITHDRAWN).count(), 0)

    # -- never breaks the move --

    def test_a_status_move_still_succeeds_when_queuing_raises(self):
        self.go(Status.DIAGNOSED)
        with mock.patch("messaging.services.queue_workorder", side_effect=RuntimeError("boom")), self.assertLogs("workorders.services", level="ERROR") as logs:
            self.go(Status.QUOTED)
        self.assertEqual(self.order_.status, Status.QUOTED)
        self.assertEqual(WorkOrder.objects.get(pk=self.order_.pk).status, Status.QUOTED)
        self.assertTrue(any("boom" in line for line in logs.output))
        self.assertEqual(self.messages().count(), 0)

    def test_a_delivery_and_its_invoice_stand_when_queuing_raises(self):
        self.go(Status.DIAGNOSED, Status.APPROVED, Status.IN_REPAIR, Status.READY)
        with mock.patch("messaging.services.queue_workorder", side_effect=RuntimeError("boom")), self.assertLogs("workorders.services", level="ERROR"):
            self.deliver()
        self.assertEqual(self.order_.status, Status.DELIVERED)
        self.assertEqual(self.order_.invoice.status, "posted")
        self.assertGreater(self.order_.invoice.total_amount, D("0"))

    def test_a_failure_to_read_the_link_never_breaks_either(self):
        with mock.patch.object(services, "customer_link", side_effect=RuntimeError("nope")), self.assertLogs("workorders.services", level="ERROR"):
            self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertEqual(self.order_.status, Status.QUOTED)

    def test_nothing_is_queued_when_the_move_rolls_back(self):
        from django.db import transaction

        class Boom(Exception):
            pass

        self.go(Status.DIAGNOSED)
        with self.assertRaises(Boom), self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                services.move(self.order_, Status.QUOTED, self.owner)
                raise Boom()
        self.assertEqual(callbacks, [])
        self.assertEqual(self.messages().count(), 0)
        self.assertEqual(WorkOrder.objects.get(pk=self.order_.pk).status, Status.DIAGNOSED)

    # -- no money, stock or ledger --

    def test_quoted_and_ready_messages_change_no_money_stock_or_ledger(self):
        self.go(Status.DIAGNOSED)
        before = self.money()
        self.go(Status.QUOTED, Status.APPROVED, Status.IN_REPAIR, Status.READY)
        self.assertEqual(len(self.keys()), 2)
        self.assertEqual(self.money(), before)

    # -- the outbox and the (mock) WhatsApp path --

    def test_the_outbox_sends_it_through_the_mock_whatsapp_path(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        messaging.save_template(MessageKind.WORKORDER, {"text": messaging.template(MessageKind.WORKORDER).text, "active": "1", "every_days": "1",
                                                         "wa_template": "hesba_workorder"}, self.owner)
        fake = FakeGraph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", fake):
            self.assertEqual(messaging.send_by_api(self.owner), {"sent": 1, "failed": 0, "left": 0})
        call = fake.calls[0]
        self.assertEqual((call["body"]["to"], call["body"]["template"]["name"]), ("201001234567", "hesba_workorder"))
        sent_text = call["body"]["template"]["components"][0]["parameters"][1]["text"]
        self.link_in(sent_text)
        message = self.one("quoted")
        self.assertEqual((message.status, message.channel, message.provider_id), (OutboxStatus.SENT, "api", "wamid.ABC"))

    def test_a_waiting_message_moves_to_today_and_is_withdrawn_when_the_order_moved_on(self):
        from datetime import timedelta

        from django.utils import timezone

        self.go(Status.DIAGNOSED, Status.QUOTED)
        OutboxMessage.objects.update(day=timezone.localdate() - timedelta(days=2))
        messaging.build()
        self.assertEqual(self.one("quoted").day, timezone.localdate())  # still quoted: still worth sending, and visible today
        self.go(Status.APPROVED)
        messaging.build()
        self.assertEqual(self.keys(), [])  # the customer already approved: no "approve the quote" message left
        self.assertEqual(self.one("quoted").status, OutboxStatus.WITHDRAWN)

    def test_a_message_already_sent_is_left_alone_by_the_housekeeping(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        OutboxMessage.objects.update(status=OutboxStatus.SENT)
        self.go(Status.APPROVED)
        messaging.build()
        self.assertEqual(self.messages().count(), 1)

    # -- review fixes --

    @staticmethod
    def url_in(text):
        return [word for word in text.split() if word.startswith(BASE)][0]

    @staticmethod
    def token_of(url):
        return url.split("?")[0].rstrip("/").split("/")[-1]

    def link_in(self, text, order=None):
        """The customer link in ``text``, checked to open this order.

        A fresh ``customer_link()`` is not compared as a string: the signed token carries the second it was made, so
        a link built a moment later can differ (seen on CI). The link must start at PUBLIC_BASE_URL and read back
        as this order's work-order token."""

        from printing.share import read_token

        link = self.url_in(text)
        self.assertTrue(link.startswith(f"{BASE}/share/"), link)
        self.assertEqual(read_token(self.token_of(link)), ("work_order", (order or self.order_).pk))
        return link

    def test_the_untouched_arabic_default_is_swapped_for_the_english_one_in_an_english_business(self):
        saved = messaging.save_template(MessageKind.WORKORDER, {"text": messaging.DEFAULT_TEXTS[MessageKind.WORKORDER], "active": "1", "every_days": "1"}, self.owner)
        self.assertEqual(saved.text, messaging.DEFAULT_TEXTS[MessageKind.WORKORDER])
        ClientProfile.objects.update(default_language="en")
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertTrue(self.one("quoted").text.startswith(f"Hello {self.customer.name}, from Demo Store: work order {self.order_.number} the quote is ready"))

    def test_a_carried_message_gets_a_fresh_valid_link_when_the_old_one_expired(self):
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone

        from printing.share import max_age, read_token

        old = timezone.now().timestamp() - (max_age() + 5 * 86400)
        self.go(Status.DIAGNOSED)
        with patch("django.core.signing.time.time", return_value=old):
            self.go(Status.QUOTED)
        stale = self.one("quoted")
        stale_url = self.url_in(stale.text)
        self.assertIsNone(read_token(self.token_of(stale_url)))  # the link queued long ago no longer opens
        OutboxMessage.objects.filter(pk=stale.pk).update(day=timezone.localdate() - timedelta(days=max_age() // 86400 + 5))
        messaging.build()
        fresh = self.one("quoted")
        self.assertEqual(fresh.day, timezone.localdate())
        fresh_url = self.url_in(fresh.text)
        self.assertNotEqual(fresh_url, stale_url)
        self.assertEqual(read_token(self.token_of(fresh_url)), ("work_order", self.order_.pk))
        self.assertIn(self.order_.number, fresh.text)

    def test_a_row_with_a_malformed_key_is_deleted_and_the_outbox_still_builds(self):
        from django.utils import timezone

        for index, key in enumerate(("workorder", "workorder:x:quoted", "workorder:1:2:3", f"workorder:{self.order_.pk}:bogus")):
            OutboxMessage.objects.create(kind=MessageKind.WORKORDER, key=key, day=timezone.localdate(), name="x", phone=f"2010000000{index}", text="t")
        messaging.build()
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.WORKORDER).count(), 0)

    def test_switching_the_workorder_kind_off_leaves_the_other_kinds_waiting_messages_alone(self):
        from django.utils import timezone

        today = timezone.localdate()
        for kind in (MessageKind.DUE, MessageKind.INSTALMENT, MessageKind.APPOINTMENT, MessageKind.REFILL):
            OutboxMessage.objects.create(kind=kind, key=f"{kind}:1", day=today, name="x", phone="201000000000", text="t")
        self.go(Status.DIAGNOSED, Status.QUOTED)
        messaging.save_template(MessageKind.WORKORDER, {"text": messaging.template(MessageKind.WORKORDER).text, "active": "", "every_days": "1"}, self.owner)
        messaging._carry_workorders(today)
        self.assertEqual(self.keys(), [])
        self.assertEqual(sorted(OutboxMessage.objects.exclude(status=OutboxStatus.WITHDRAWN).values_list("kind", flat=True)), ["appointment", "due", "instalment", "refill"])

    # -- Codex review on #306 --

    def test_withdrawing_an_obsolete_message_keeps_the_once_per_status_record(self):
        self.go(Status.DIAGNOSED, Status.IN_REPAIR, Status.READY)
        first = self.one("ready")
        self.go(Status.IN_REPAIR)
        messaging.build()  # the order moved on: the waiting READY message is withdrawn
        first.refresh_from_db()
        self.assertEqual((first.status, first.text), (OutboxStatus.WITHDRAWN, ""))
        self.go(Status.READY)
        messaging.build()
        self.assertEqual(self.messages().filter(key=f"workorder:{self.order_.pk}:ready").count(), 1)  # never a second READY message
        self.assertEqual(self.keys(), [])  # and the withdrawn one is not sendable
        self.assertEqual(messaging.send_by_api(self.owner), {"sent": 0, "failed": 0, "left": 0})
        with self.assertRaises(Exception):
            messaging.mark(first, OutboxStatus.SENT, self.owner)

    def test_a_withdrawn_message_is_not_listed_in_the_outbox(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.go(Status.APPROVED)
        self.client.force_login(self.owner)
        for show in ("pending", "sent", "skipped", "withdrawn"):
            page = self.client.get(reverse("messaging:outbox") + f"?lang=ar&show={show}")
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, f'data-message="{self.one("quoted").pk}"')

    def test_a_waiting_message_goes_to_the_customers_current_number_each_build(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.assertEqual(self.one("quoted").phone, "201001234567")
        self.customer.phone = "01255556666"
        self.customer.save()
        messaging.build()  # same day: the number is still refreshed
        self.assertEqual(self.one("quoted").phone, "201255556666")
        self.customer.whatsapp = "01112223334"
        self.customer.save()
        messaging.build()
        self.assertEqual(self.one("quoted").phone, "201112223334")

    def test_a_waiting_message_is_withdrawn_when_no_valid_number_remains(self):
        self.go(Status.DIAGNOSED, Status.QUOTED)
        self.customer.phone = ""
        self.customer.whatsapp = ""
        self.customer.save()
        messaging.build()
        message = self.one("quoted")
        self.assertEqual((message.status, message.text), (OutboxStatus.WITHDRAWN, ""))
        self.assertEqual(self.keys(), [])
        self.customer.phone = "01001234567"
        self.customer.save()
        self.go(Status.APPROVED)  # the withdrawn record also stops a second QUOTED message from ever queuing
        messaging.build()
        self.assertEqual(self.messages().filter(key__endswith=":quoted").count(), 1)

    # -- the customer link is counter-only, in the outbox too --

    def outsider(self, code, username):
        from hesba_testing.factories import grant, make_role, make_user, make_user_profile
        from permissions.models import Permission

        user = make_user(username=username)
        role = make_role(f"ROLE-{username}")
        grant(role, Permission.objects.get(code=code))
        make_user_profile(user=user, role=role)
        return user

    def test_a_customer_facing_user_who_is_not_counter_staff_never_sees_or_sends_the_link(self):
        viewer = self.outsider("sales.view_sales_invoices", "wo_outbox_viewer")
        self.assertFalse(services.can_hold_customer_link(viewer))
        self.assertTrue(services.can_hold_customer_link(self.owner))
        self.go(Status.DIAGNOSED, Status.QUOTED)
        message = self.one("quoted")
        link = self.link_in(message.text)
        url = reverse("messaging:outbox")
        self.client.force_login(viewer)
        for lang, mask in (("ar", "[رابط العميل]"), ("en", "[customer link]")):
            page = self.client.get(url + f"?lang={lang}")
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, link)
            self.assertNotContains(page, self.token_of(link))
            self.assertContains(page, mask)
        self.assertEqual(self.client.post(url, {"lang": "ar", "action": "send", "message": message.pk}).status_code, 403)
        self.assertEqual(OutboxMessage.objects.get(pk=message.pk).status, OutboxStatus.PENDING)
        # The sent history masks it too.
        OutboxMessage.objects.filter(pk=message.pk).update(status=OutboxStatus.SENT)
        history = self.client.get(url + "?lang=ar&show=sent")
        self.assertNotContains(history, link)
        self.assertContains(history, "[رابط العميل]")
        # Counter staff still read it whole and send it.
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url + "?lang=ar&show=sent"), link)

    def test_counter_staff_still_send_it_and_an_outsider_cannot_send_it_in_bulk(self):
        viewer = self.outsider("sales.receive_customer_payment", "wo_outbox_cashier")
        self.go(Status.DIAGNOSED, Status.QUOTED)
        messaging.save_template(MessageKind.WORKORDER, {"text": messaging.template(MessageKind.WORKORDER).text, "active": "1", "every_days": "1",
                                                         "wa_template": "hesba_workorder"}, self.owner)
        fake = FakeGraph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", fake):
            self.client.force_login(viewer)
            self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send_all"})
            self.assertEqual(fake.calls, [])
            self.assertEqual(self.one("quoted").status, OutboxStatus.PENDING)
            self.client.force_login(self.owner)
            self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send_all"})
            self.assertEqual(len(fake.calls), 1)

    def test_a_message_without_a_link_is_not_hidden_from_a_customer_facing_user(self):
        viewer = self.outsider("sales.view_sales_invoices", "wo_outbox_viewer2")
        self.go(Status.DIAGNOSED, Status.IN_REPAIR, Status.READY)
        self.deliver()
        message = self.one("delivered")
        self.assertNotIn("://", message.text)
        self.client.force_login(viewer)
        self.assertContains(self.client.get(reverse("messaging:outbox") + "?lang=ar"), message.text.split()[0])
