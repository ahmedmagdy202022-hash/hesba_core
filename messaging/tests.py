"""T9 (MARKET-PARITY): today's customer messages, gathered once, sent through WhatsApp or skipped (HG-064)."""

from datetime import timedelta
from decimal import Decimal as D
from unittest import mock
from urllib.parse import unquote

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from master_data.models import Customer
from permissions.models import RoleCode
from reports.tests_aging import owe, pay
from reports.tests_dashboard import prepared_client

from . import services
from .models import MessageKind, OutboxMessage, OutboxStatus

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class OutboxSetup(TestCase):
    def setUp(self):
        prepared_client("commercial", "wholesale")
        self.owner = person(RoleCode.OWNER, "ms_owner")
        self.cashier = person(RoleCode.CASHIER, "ms_cashier")
        self.ali = make_customer(customer_code="ALI-MS", name="علي حسن", phone="01001234567")
        Customer.objects.filter(pk=self.ali.pk).update(created_at=timezone.now() - timedelta(days=200))
        owe(self.ali, "750.00", TODAY - timedelta(days=60))
        self.quiet = make_customer(customer_code="NO-PHONE", name="من غير رقم", phone="")
        owe(self.quiet, "100.00", TODAY - timedelta(days=60))


class BuildTests(OutboxSetup):
    def test_an_overdue_customer_gets_one_message_with_the_amount(self):
        self.assertEqual(services.build(), 1)
        message = OutboxMessage.objects.get()
        self.assertEqual((message.kind, message.phone, message.status), (MessageKind.DUE, "201001234567", OutboxStatus.PENDING))
        self.assertIn("750.00", message.text)
        self.assertIn("علي حسن", message.text)
        self.assertEqual(services.build(), 0)  # the same day again adds nothing

    def test_a_reminded_customer_waits_every_days_before_the_next_one(self):
        services.build()
        services.mark(OutboxMessage.objects.get(), OutboxStatus.SENT, self.owner)
        self.assertEqual(services.build(TODAY + timedelta(days=3)), 0)  # within the 7 days
        self.assertEqual(services.build(TODAY + timedelta(days=7)), 1)

    def test_a_message_left_waiting_moves_to_the_next_day_and_is_not_queued_twice(self):
        services.build()
        first = OutboxMessage.objects.get()
        tomorrow = TODAY + timedelta(days=1)
        self.assertEqual(services.build(tomorrow), 0)  # nothing new: the same reminder, still waiting
        first.refresh_from_db()
        self.assertEqual((first.day, first.status), (tomorrow, OutboxStatus.PENDING))  # still on the outbox tomorrow
        self.assertEqual(OutboxMessage.objects.count(), 1)
        services.mark(first, OutboxStatus.SENT, self.owner)
        self.assertEqual(services.build(tomorrow + timedelta(days=1)), 0)  # sent now: the 7 days start

    def test_a_payment_after_gathering_updates_or_withdraws_the_waiting_message(self):
        services.build()
        message = OutboxMessage.objects.get()
        pay(self.ali, "250.00", TODAY)
        services.build()
        message.refresh_from_db()
        self.assertIn("500.00", message.text)  # today's figure, not the 750 it was queued with
        self.client.force_login(self.cashier)
        self.client.get(reverse("messaging:outbox") + "?lang=ar")
        pay(self.ali, "500.00", TODAY)  # paid in full while the page was open
        response = self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send", "message": message.pk})
        self.assertContains(response, "مبقتش لازمة")
        self.assertFalse(OutboxMessage.objects.exists())  # withdrawn, never sent

    def test_a_second_entity_keeps_the_main_entitys_reminders_as_they_were(self):
        from entities.models import Entity
        from entities.services import main_entity

        services.build()
        first = OutboxMessage.objects.get()
        self.assertEqual((first.key, first.entity), (f"due:customer:{self.ali.pk}", main_entity()))
        services.mark(first, OutboxStatus.SENT, self.owner)
        Entity.objects.create(code="E-MS2", name_ar="فرع تاني")
        self.assertEqual(services.build(TODAY + timedelta(days=1)), 0)  # still within its 7 days: no second reminder
        OutboxMessage.objects.all().delete()
        services.build()
        waiting = OutboxMessage.objects.get()
        OutboxMessage.objects.filter(pk=waiting.pk).update(entity=None)  # queued before entities were recorded
        pay(self.ali, "750.00", TODAY)
        services.build()
        self.assertFalse(OutboxMessage.objects.exists())  # paid: withdrawn, whatever entity it was queued under

    def test_each_entity_gets_its_own_messages_and_no_one_sends_another_entitys(self):
        from entities.models import Entity, EntityMembership
        from entities.services import main_entity

        branch = Entity.objects.create(code="E-MS", name_ar="فرع الرسائل")
        services.build()
        message = OutboxMessage.objects.get()  # the debt sits in the main entity; the branch has none
        self.assertEqual(message.entity, main_entity())
        outsider = person(RoleCode.CASHIER, "ms_branch")
        EntityMembership.objects.create(user=outsider, entity=branch, is_default=True)
        self.assertFalse(services.visible(outsider).exists())
        with self.assertRaises(PermissionDenied):
            services.mark(message, OutboxStatus.SENT, outsider)
        self.client.force_login(outsider)
        self.assertNotContains(self.client.get(reverse("messaging:outbox") + "?lang=ar"), f'data-send="{message.pk}"')
        self.assertEqual(self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send", "message": message.pk}).status_code, 404)
        message.refresh_from_db()
        self.assertEqual(message.status, OutboxStatus.PENDING)
        self.assertTrue(services.visible(self.owner).filter(pk=message.pk).exists())

    def test_a_switched_off_kind_sends_nothing_and_unknown_words_are_refused(self):
        services.save_template(MessageKind.DUE, {"text": "تذكير {name}", "every_days": "7"}, self.owner)  # active unticked
        self.assertEqual(services.build(), 0)
        with self.assertRaises(ValidationError):
            services.save_template(MessageKind.DUE, {"text": "تذكير {customer}", "active": "1"}, self.owner)
        with self.assertRaises(PermissionDenied):
            services.save_template(MessageKind.DUE, {"text": "تذكير {name}", "active": "1"}, self.cashier)

    def test_tomorrows_appointment_and_a_refill_due_are_gathered(self):
        from appointments.models import Appointment
        from pharmacy import services as pharmacy
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        profile = ClientProfile.objects.get()
        set_module_enabled(profile, "appointments_visits", True, self.owner)
        caps.set_capability_enabled(profile, "drugs", True)
        tomorrow = timezone.make_aware(timezone.datetime.combine(TODAY + timedelta(days=1), timezone.datetime.min.time())) + timedelta(hours=11)
        Appointment.objects.create(number="AP-1", customer=self.ali, starts_at=tomorrow, duration_minutes=30, created_by=self.owner)
        rx = pharmacy.create_prescription({"patient_name": "سعاد", "patient_phone": "01112223334", "refill_days": "30"}, self.owner)
        with mock.patch("pharmacy.services.due", return_value=[rx]), mock.patch("pharmacy.services.next_refill", return_value=TODAY + timedelta(days=2)):
            services.build()
        kinds = set(OutboxMessage.objects.values_list("kind", flat=True))
        self.assertEqual(kinds, {MessageKind.DUE, MessageKind.APPOINTMENT, MessageKind.REFILL})
        self.assertIn("11:00", OutboxMessage.objects.get(kind=MessageKind.APPOINTMENT).text)

    def test_the_daily_command(self):
        call_command("gather_messages", stdout=mock.MagicMock())
        self.assertEqual(OutboxMessage.objects.count(), 1)


class ScreenTests(OutboxSetup):
    def test_sending_opens_whatsapp_with_the_text_and_marks_it_sent(self):
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        message = OutboxMessage.objects.get()
        self.assertContains(page, f'data-send="{message.pk}"')
        self.assertNotContains(page, "data-template=")  # the wording is the owner's
        self.assertNotContains(page, f'href="/parties/customer/{self.ali.pk}/')  # the cashier cannot open the customer card (crawl)
        response = self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send", "message": message.pk})
        self.assertTrue(response["Location"].startswith("https://wa.me/201001234567?text="))
        self.assertIn("750.00", unquote(response["Location"]))
        message.refresh_from_db()
        self.assertEqual((message.status, message.channel, message.sent_by), (OutboxStatus.SENT, "link", self.cashier))
        again = self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send", "message": message.pk})
        self.assertEqual(again.status_code, 200)  # already sent: refused, not sent twice

    def test_skip_and_templates_in_both_languages(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(reverse("messaging:outbox") + f"?lang={lang}"), 'data-template="due"')
        message = OutboxMessage.objects.get()
        self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "skip", "message": message.pk})
        message.refresh_from_db()
        self.assertEqual(message.status, OutboxStatus.SKIPPED)
        self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "template", "kind": "due", "text": "يا {name} عليك {amount}",
                                                       "every_days": "14", "active": "1"})
        self.assertEqual(services.template(MessageKind.DUE).every_days, 14)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/messages/?lang=ar"')

    def test_a_stock_keeper_does_not_see_customer_messages(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "ms_keeper"))
        self.assertEqual(self.client.get(reverse("messaging:outbox")).status_code, 403)
