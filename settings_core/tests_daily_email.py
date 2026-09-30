"""DIGEST-002: the daily follow-up email — content per permission, schedule, screen, trigger."""

from datetime import datetime, timedelta
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.followup import followup
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice

from . import daily_email

TODAY = timezone.localdate()


def person(role_code, username, email=""):
    user = make_user(username=username, email=email)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def at(hour, minute=0, day=None):
    return timezone.make_aware(datetime.combine(day or TODAY, datetime.min.time().replace(hour=hour, minute=minute)))


class DailyEmailSetup(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "mail_owner", "owner@shop.test")
        self.cashier = person(RoleCode.CASHIER, "mail_cashier", "cashier@shop.test")
        self.location = make_location(location_code="MAIN", is_default=True)
        make_item(item_code="OUT-1", item_name="زيت", is_stock_tracked=True)  # no stock: out of stock
        SalesInvoice.objects.create(invoice_number="SI-DRAFT-1", invoice_date=TODAY, customer=make_customer(), selling_location=self.location, created_by=self.owner)
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=self.owner, module="sales", action="cancel_posted_sales_invoice",
                                object_type="sales.SalesInvoice", object_id="1", before_data={}, after_data={})


class ContentTests(DailyEmailSetup):
    def test_follow_up_lists_what_needs_doing_and_the_days_events(self):
        data = followup(self.owner, TODAY, "evening")
        keys = {item["key"] for item in data["todo"]}
        self.assertIn("out_of_stock", keys)
        self.assertIn("sales_drafts", keys)
        self.assertEqual(data["covered"], TODAY)
        self.assertEqual([e["key"] for e in data["events"]], ["cancellations"])
        self.assertEqual(followup(self.owner, TODAY, "morning")["covered"], TODAY - timedelta(days=1))

    def test_each_person_only_gets_what_they_may_see(self):
        owner = followup(self.owner, TODAY, "evening")
        cashier = followup(self.cashier, TODAY, "evening")
        self.assertIn("profit", owner["digest"])
        self.assertNotIn("profit", cashier["digest"])  # no profit report permission
        self.assertNotIn("out_of_stock", {item["key"] for item in cashier["todo"]})  # no inventory report
        self.assertEqual(cashier["events"], [])  # cancellations are for managers

    @override_settings(PUBLIC_BASE_URL="https://shop.example.com")
    def test_the_email_has_html_text_and_links(self):
        message = daily_email.build_message(self.owner, "evening", TODAY, "ar")  # the evening email covers today's events
        self.assertIn("ملخص آخر اليوم", message.subject)
        self.assertEqual(message.to, ["owner@shop.test"])
        html = message.alternatives[0][0]
        self.assertIn('data-todo="out_of_stock"', html)
        self.assertIn('data-event="cancellations"', html)
        self.assertIn("https://shop.example.com/reports/inventory/?lang=ar", html)
        self.assertIn("المطلوب متابعته", message.body)
        self.assertIn("SI-DRAFT-1", message.body)


class ScheduleTests(DailyEmailSetup):
    def configure(self, slots=("morning", "evening")):
        daily_email.save_config(slots=list(slots), hours={"morning": 8, "evening": 21}, recipients=[self.owner.pk, self.cashier.pk], lang="ar", user=self.owner)

    def test_a_slot_is_sent_once_inside_its_window(self):
        self.configure()
        self.assertEqual(daily_email.run_due(at(7, 59))["status"], "idle")
        report = daily_email.run_due(at(8, 5))
        self.assertEqual((report["status"], report["slots"]["morning"]["sent"]), ("sent", 2))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["cashier@shop.test", "owner@shop.test"])
        self.assertEqual(daily_email.run_due(at(9, 5))["slots"], {})  # already sent today
        self.assertEqual(daily_email.run_due(at(20, 0))["slots"], {})  # never a stale morning email at night
        self.assertEqual(daily_email.run_due(at(21, 30))["slots"]["evening"]["sent"], 2)
        self.assertEqual(len(mail.outbox), 4)
        self.assertEqual(daily_email.config()["last"], {"morning": TODAY.isoformat(), "evening": TODAY.isoformat()})

    def test_only_chosen_slots_and_users_with_email(self):
        self.configure(slots=("evening",))
        self.assertEqual(daily_email.run_due(at(8, 30))["slots"], {})
        no_email = person(RoleCode.MANAGER, "mail_nomail")
        daily_email.save_config(slots=["evening"], hours={"morning": 8, "evening": 21}, recipients=[no_email.pk, self.owner.pk], lang="ar", user=self.owner)
        self.assertEqual(daily_email.config()["recipients"], [self.owner.pk])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_nothing_is_sent_when_email_is_not_set_up(self):
        self.configure()
        self.assertEqual(daily_email.run_due(at(8, 5))["status"], "not_configured")
        self.assertEqual(len(mail.outbox), 0)

    def test_management_command(self):
        self.configure()
        call_command("send_daily_email", "--slot", "evening", "--force", stdout=mock.MagicMock())
        self.assertEqual(len(mail.outbox), 2)


@override_settings(NIGHTLY_TOKEN="t0ken-for-tests")
class TriggerAndScreenTests(DailyEmailSetup):
    def test_the_hourly_trigger_needs_the_token(self):
        self.assertEqual(self.client.get(reverse("ops_digest")).status_code, 404)
        with mock.patch("settings_core.daily_email.run_due", return_value={"status": "sent", "slots": {"morning": {"sent": 1, "failed": []}}}):
            answer = self.client.get(reverse("ops_digest"), HTTP_X_HESBA_TOKEN="t0ken-for-tests")
        self.assertEqual(answer.json(), {"status": "sent", "sent": {"morning": 1}})

    def test_the_owner_sets_it_up_and_sends_a_test(self):
        self.client.force_login(self.owner)
        url = reverse("settings_core:daily_email")
        page = self.client.get(url)
        self.assertContains(page, 'data-recipient="mail_cashier"')
        self.assertContains(page, "data-email-off")
        self.client.post(url, {"action": "save", "slots": ["morning"], "morning_hour": "7", "evening_hour": "21", "recipients": [str(self.owner.pk)], "email_lang": "ar"})
        conf = daily_email.config()
        self.assertEqual((conf["slots"], conf["hours"]["morning"], conf["recipients"]), (["morning"], 7, [self.owner.pk]))
        self.client.post(url, {"action": "test", "slot": "evening"})
        self.assertEqual([m.to for m in mail.outbox], [["owner@shop.test"]])
        self.assertIn("ملخص آخر اليوم", mail.outbox[0].subject)
        self.assertEqual(daily_email.config()["last"]["evening"], "")  # a test is not a scheduled send
        self.assertContains(self.client.get(reverse("settings_core:overview")), "data-daily-email-link")

    def test_only_managers_change_it(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(reverse("settings_core:daily_email"), {"action": "save", "slots": ["morning"]}).status_code, 403)
        self.assertEqual(daily_email.config()["slots"], [])


class UserEmailTests(DailyEmailSetup):
    def test_the_owner_records_a_users_email_from_the_users_screen(self):
        self.client.force_login(self.owner)
        keeper = person(RoleCode.STOCK_KEEPER, "mail_keeper")
        url = reverse("settings_core:user_edit", args=[keeper.pk])
        self.assertContains(self.client.get(url), 'name="email"')
        self.client.post(url, {"display_name": "أمين المخزن", "phone": "", "email": "Keeper@Shop.test", "role": str(keeper.hesba_profile.role_id), "active": "on"})
        keeper.refresh_from_db()
        self.assertEqual(keeper.email, "keeper@shop.test")
        self.assertTrue(AuditLog.objects.filter(action="update_user", after_data__email="keeper@shop.test").exists())
        page = self.client.get(reverse("settings_core:daily_email"))
        self.assertNotContains(page, 'value="%d" disabled' % keeper.pk)  # can now be chosen as a recipient
