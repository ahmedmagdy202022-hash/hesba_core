"""A1 (HG-079): the voice summary of the day. Every test runs on the AI no-network base, in mock mode.

A shop with, dated yesterday, two posted sales (200.00 + 50.50 = 250.50) and a collection of 75.25; dated today, a
sale of 999.00 and a collection of 10.00 (neither belongs in "yesterday"); one item at or below its minimum and one out
of stock.
"""

from datetime import datetime, time as dt_time, timedelta, timezone as dt_timezone
from decimal import Decimal as D
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from entities.current import working_in
from entities.models import Entity, EntityMembership
from entities.tests import person
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_role, make_user, make_user_profile, stock_in
from permissions.models import RoleCode
from reports import selectors
from reports.dashboard_data import DashboardFigures
from reports.tests_dashboard import prepared_client, sell
from sales.models import SalesInvoiceStatus
from sales.services import record_customer_payment
from settings_core import capabilities as caps
from settings_core.models import FeatureFlag

from . import providers, services, voice_summary
from .models import VoiceSummaryAudio
from .no_network import AiTestCase
from .providers.mock import SILENT_MP3, MockElevenLabsTTS
from .services import CAIRO
from .summary import build_summary

MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports"
ENDPOINT = "/ai/voice-summary/"

AR_TEXT = ("أهلاً، ده ملخص النهارده. امبارح المبيعات كانت 250.50 جنيه مصري من 2 فاتورة. والتحصيل كان 75.25 جنيه مصري. "
           "فيه 1 صنف قربت تخلص، و1 صنف خلصت من المخزن.")
EN_TEXT = ("Hello, here is today's summary. Sales yesterday came to 250.50 Egyptian pounds from 2 invoices. "
           "Collections came to 75.25 Egyptian pounds. 1 item is running low, and 1 item is out of stock.")


def nobody(username):
    """A signed-in user whose role grants none of the dashboard's figures."""

    user = make_user(username=username)
    make_user_profile(user=user, role=make_role(code=f"R-{username}"))
    return user


class VoiceSummaryBase(AiTestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client("commercial", "retail", MODULES)
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.owner = person(RoleCode.OWNER, "voice_owner")
        cls.location = make_location(location_code="V-MAIN")
        cls.cashbox = make_cashbox(cashbox_code="V-CASH")
        cls.item = make_item(item_code="V-ITEM", item_name="صنف البيع")
        stock_in(cls.item, cls.location, 20, "10.00")
        low = make_item(item_code="V-LOW", item_name="صنف قليل", min_stock=D("5"))
        stock_in(low, cls.location, 2, "10.00")
        make_item(item_code="V-OUT", item_name="صنف خلصان")
        sell(cls.item, cls.location, cls.cashbox, "2", "100.00", "0.00", cls.owner, number="SI-Y1", when=cls.yesterday)
        sell(cls.item, cls.location, cls.cashbox, "1", "50.50", "0.00", cls.owner, number="SI-Y2", when=cls.yesterday)
        sell(cls.item, cls.location, cls.cashbox, "1", "999.00", "0.00", cls.owner, number="SI-T1", when=cls.today)
        customer = make_customer()
        record_customer_payment("CP-Y1", cls.yesterday, customer, cls.cashbox, D("75.25"), user=cls.owner)
        record_customer_payment("CP-T1", cls.today, customer, cls.cashbox, D("10.00"), user=cls.owner)

    def setUp(self):
        super().setUp()
        self.provider = MockElevenLabsTTS(voice_id="test-voice", model_id="eleven_multilingual_v2")
        patcher = mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: lambda: self.provider})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.switch(True)

    def switch(self, enabled):
        FeatureFlag.objects.update_or_create(code=caps.flag_code("ai_voice_summary"), defaults={"name": "Voice summary", "enabled": enabled})

    def login(self, user=None):
        self.client.force_login(user or self.owner)

    def post(self, lang="ar", client=None):
        return (client or self.client).post(ENDPOINT, {"lang": lang})

    def calls(self):
        return len(self.provider.calls)


class SummaryTextTests(VoiceSummaryBase):
    def test_the_arabic_text_is_the_report_figures_exactly(self):
        summary = build_summary(self.owner, "ar")
        self.assertEqual(summary.text, AR_TEXT)
        # The same figures the reports and the dashboard give for yesterday.
        posted = selectors.sales_report(self.yesterday, self.yesterday, SalesInvoiceStatus.POSTED)
        self.assertEqual(sum(invoice.total_amount for invoice in posted), D("250.50"))
        self.assertEqual(posted.count(), 2)
        figures = DashboardFigures(self.owner, self.yesterday)
        self.assertEqual(figures.value_for("sales_today", "all"), D("250.50"))
        self.assertEqual(figures.value_for("receipts_today", "all"), D("75.25"))
        self.assertEqual(selectors.stock_alert_counts(), {"low_stock": 1, "out_of_stock": 1})
        self.assertNotIn("999", summary.text)  # today's sale is not yesterday's
        self.assertNotIn("10.00", summary.text)  # nor today's collection

    def test_the_english_text_and_the_figures_with_their_links(self):
        summary = build_summary(self.owner, "en")
        self.assertEqual(summary.text, EN_TEXT)
        tiles = {tile["key"]: tile for tile in summary.tiles}
        self.assertEqual(list(tiles), ["sales", "receipts", "low_stock"])
        self.assertEqual(tiles["sales"]["value"], "250.50 EGP")
        self.assertEqual(tiles["sales"]["sub"], "2 invoices")
        self.assertEqual(tiles["receipts"]["value"], "75.25 EGP")
        self.assertEqual(tiles["low_stock"]["value"], "1 item")
        self.assertEqual(tiles["low_stock"]["tone"], "warn")
        y = self.yesterday.isoformat()
        self.assertEqual(tiles["sales"]["url"], f"{reverse('reports:sales')}?date_from={y}&date_to={y}&status=posted&lang=en")
        self.assertEqual(tiles["low_stock"]["url"], f"{reverse('reports:inventory')}?lang=en")
        self.assertEqual(tiles["receipts"]["url"], f"{reverse('sales:payments')}?status=posted&lang=en")
        ar = {tile["key"]: tile for tile in build_summary(self.owner, "ar").tiles}
        self.assertEqual(ar["sales"]["value"], "250.50 ج.م")  # the screen's currency symbol; the voice says the name
        self.assertEqual(ar["low_stock"]["value"], "1 صنف")

    def test_the_text_holds_no_time_of_day(self):
        morning = datetime.combine(self.today, dt_time(8, 0), tzinfo=CAIRO)
        evening = datetime.combine(self.today, dt_time(21, 0), tzinfo=CAIRO)
        with mock.patch("django.utils.timezone.now", return_value=morning):
            first = build_summary(self.owner, "ar").text
        with mock.patch("django.utils.timezone.now", return_value=evening):
            self.assertEqual(build_summary(self.owner, "ar").text, first)

    def test_today_s_appointments_when_the_module_is_open(self):
        from appointments.models import Appointment, AppointmentStatus
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        self.assertNotIn("مواعيد", build_summary(self.owner, "ar").text)  # the module is off: left out
        set_module_enabled(ClientProfile.get_active(), "appointments_visits", True)
        noon = timezone.make_aware(datetime.combine(self.today, dt_time(12, 0)))
        customer = make_customer()
        for number, status in (("AP-1", AppointmentStatus.BOOKED), ("AP-2", AppointmentStatus.DONE), ("AP-3", AppointmentStatus.CANCELLED)):
            Appointment.objects.create(number=number, customer=customer, starts_at=noon, status=status, created_by=self.owner)
        Appointment.objects.create(number="AP-T", customer=customer, starts_at=noon + timedelta(days=1), created_by=self.owner)
        summary = build_summary(self.owner, "ar")
        self.assertTrue(summary.text.endswith("والنهارده عندك 2 ميعاد."))  # cancelled and tomorrow's are not counted
        self.assertEqual({tile["key"]: tile["value"] for tile in summary.tiles}["appointments"], "2")
        self.assertTrue(build_summary(self.owner, "en").text.endswith("You have 2 appointments today."))


class PermissionTests(VoiceSummaryBase):
    def test_a_figure_the_listener_may_not_see_is_left_out_not_zeroed(self):
        keeper = person(RoleCode.STOCK_KEEPER, "voice_keeper")
        summary = build_summary(keeper, "ar")
        self.assertEqual(summary.text, "أهلاً، ده ملخص النهارده. فيه 1 صنف قربت تخلص، و1 صنف خلصت من المخزن.")
        self.assertEqual([tile["key"] for tile in summary.tiles], ["low_stock"])
        for absent in ("250.50", "75.25", "0.00", "المبيعات", "التحصيل"):
            self.assertNotIn(absent, summary.text)

    def test_a_cashier_hears_their_own_sales_only(self):
        cashier = person(RoleCode.CASHIER, "voice_cashier")
        sell(self.item, self.location, self.cashbox, "1", "12.00", "0.00", cashier, number="SI-C1", when=self.yesterday)
        summary = build_summary(cashier, "ar")
        self.assertIn("امبارح مبيعاتك كانت 12.00 جنيه مصري من 1 فاتورة.", summary.text)
        self.assertNotIn("262.50", summary.text)  # the shop's whole day
        self.assertNotIn("قربت تخلص", summary.text)  # no stock report for a cashier
        self.assertEqual(summary.tiles[0]["label"], "مبيعاتي امبارح")

    def test_no_figure_at_all_means_no_card_and_a_refusal(self):
        support = nobody("voice_nobody")
        self.assertFalse(build_summary(support, "ar").has_figures)
        self.login(support)
        self.assertNotContains(self.client.get("/dashboard/?lang=ar"), "data-voice-summary")
        self.assertEqual(self.client.get(ENDPOINT).status_code, 403)
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "لسه مفيش أرقام نلخّصها لك.")
        self.assertEqual(self.calls(), 0)


class CairoDayTests(VoiceSummaryBase):
    def test_yesterday_is_the_cairo_day_before_not_the_utc_one(self):
        before = self.today - timedelta(days=2)
        sell(self.item, self.location, self.cashbox, "1", "7.00", "0.00", self.owner, number="SI-B1", when=before)
        last_minute = datetime.combine(self.yesterday, dt_time(23, 59), tzinfo=CAIRO).astimezone(dt_timezone.utc)
        just_after = datetime.combine(self.today, dt_time(0, 30), tzinfo=CAIRO).astimezone(dt_timezone.utc)
        self.assertEqual(just_after.date(), self.yesterday)  # still yesterday in UTC (Cairo is ahead of UTC)
        with mock.patch("django.utils.timezone.now", return_value=last_minute):
            summary = build_summary(self.owner, "ar")
            self.assertEqual(summary.day, self.yesterday)
            self.assertIn("امبارح المبيعات كانت 7.00 جنيه مصري من 1 فاتورة.", summary.text)
        with mock.patch("django.utils.timezone.now", return_value=just_after):
            summary = build_summary(self.owner, "ar")
            self.assertEqual(summary.day, self.today)
            self.assertIn("امبارح المبيعات كانت 250.50 جنيه مصري من 2 فاتورة.", summary.text)


class EntityScopeTests(VoiceSummaryBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from entities import services as entity_services

        cls.main = Entity.objects.get(is_main=True)
        cls.branch = entity_services.save_entity({"code": "BRV", "name_ar": "فرع مدينة نصر", "activity_slug": "commercial", "sub_activity_slug": "retail"}, cls.owner)
        location = make_location(location_code="V-BR", entity=cls.branch)
        cashbox = make_cashbox(cashbox_code="V-BR-CASH", entity=cls.branch)
        stock_in(cls.item, location, 10, "10.00")
        sell(cls.item, location, cashbox, "1", "444.44", "0.00", cls.owner, number="SI-BR1", when=cls.yesterday)
        cls.manager = person(RoleCode.MANAGER, "voice_branch_manager")
        EntityMembership.objects.create(user=cls.manager, entity=cls.branch, is_default=True)

    def test_a_branch_member_hears_the_branch_only(self):
        self.login(self.manager)
        data = self.client.get(ENDPOINT + "?lang=ar").json()
        self.assertIn("امبارح المبيعات كانت 444.44 جنيه مصري من 1 فاتورة.", data["text"])
        self.assertNotIn("250.50", data["text"])  # the main shop's sales
        self.assertNotIn("75.25", data["text"])  # and its collection
        self.assertIn("والتحصيل كان 0.00 جنيه مصري.", data["text"])  # the branch collected nothing: a real zero

    def test_each_entity_has_its_own_text_and_cache_scope(self):
        with working_in(self.main):
            main = build_summary(self.owner, "ar")
        with working_in(self.branch):
            branch = build_summary(self.owner, "ar")
        group = build_summary(self.owner, "ar")
        self.assertIn("250.50", main.text)
        self.assertNotIn("444.44", main.text)
        self.assertIn("444.44", branch.text)
        self.assertIn("694.94", group.text)  # the whole group
        self.assertEqual({main.scope_key, branch.scope_key, group.scope_key}, {f"entity:{self.main.pk}", f"entity:{self.branch.pk}", "group"})

    def test_the_audio_of_another_entity_is_not_served(self):
        self.login()  # the owner, whole group
        owners = self.post().json()
        self.assertTrue(owners["ok"])
        self.login(self.manager)
        self.assertEqual(self.client.get(owners["audio_url"]).status_code, 404)
        mine = self.post().json()
        self.assertNotEqual(mine["audio_url"], owners["audio_url"])
        self.assertEqual(self.client.get(mine["audio_url"]).status_code, 200)
        self.assertEqual(VoiceSummaryAudio.objects.get(scope_key=f"entity:{self.branch.pk}").entity, self.branch)
        self.assertEqual(self.calls(), 2)

    def test_appointments_are_the_branch_s_own(self):
        from appointments.models import Appointment
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(ClientProfile.get_active(), "appointments_visits", True)
        noon = timezone.make_aware(datetime.combine(self.today, dt_time(12, 0)))
        customer = make_customer()
        Appointment.objects.create(number="AP-BR", customer=customer, starts_at=noon, entity=self.branch, created_by=self.owner)
        for number in ("AP-M1", "AP-M2"):
            Appointment.objects.create(number=number, customer=customer, starts_at=noon, created_by=self.owner)  # main (no entity)
        self.login(self.manager)
        self.assertTrue(self.client.get(ENDPOINT + "?lang=ar").json()["text"].endswith("والنهارده عندك 1 ميعاد."))
        with working_in(self.main):
            self.assertTrue(build_summary(self.owner, "ar").text.endswith("والنهارده عندك 2 ميعاد."))
        self.assertTrue(build_summary(self.owner, "ar").text.endswith("والنهارده عندك 3 مواعيد."))


class CallTests(VoiceSummaryBase):
    def test_visiting_the_dashboard_and_reading_the_summary_never_calls_the_provider(self):
        self.login()
        page = self.client.get("/dashboard/?lang=ar")
        self.assertContains(page, "data-voice-summary")
        data = self.client.get(ENDPOINT + "?lang=ar").json()
        self.assertEqual((data["ok"], data["text"], data["audio_url"]), (True, AR_TEXT, ""))
        self.assertEqual([figure["key"] for figure in data["figures"]], ["sales", "receipts", "low_stock"])
        self.assertEqual((self.calls(), services.calls_today(), VoiceSummaryAudio.objects.count()), (0, 0, 0))
        self.assertFalse(AuditLog.objects.filter(module="ai").exists())

    def test_the_first_post_voices_once_and_the_second_uses_the_cache(self):
        self.login()
        first = self.post()
        self.assertEqual(first.status_code, 200)
        data = first.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["text"], AR_TEXT)
        self.assertEqual(self.provider.calls[0]["body"]["text"], AR_TEXT)
        row = VoiceSummaryAudio.objects.get()
        self.assertEqual((row.ready, row.language, row.day, row.scope_key, bytes(row.audio)), (True, "ar", self.today, "group", SILENT_MP3))
        self.assertEqual(data["audio_url"], f"/ai/voice-summary/audio/{row.text_sha256}.mp3?lang=ar")
        second = self.post().json()
        self.assertEqual(second["audio_url"], data["audio_url"])
        self.assertEqual((self.calls(), services.calls_today(), VoiceSummaryAudio.objects.count()), (1, 1, 1))
        self.assertEqual(AuditLog.objects.filter(module="ai", action=services.CALL_STARTED).count(), 1)
        # Once cached, the dashboard hands the card the audio URL, so the press plays without a POST.
        self.assertContains(self.client.get("/dashboard/?lang=ar"), f'data-audio-url="{data["audio_url"]}"')

    def test_each_language_and_each_new_text_is_its_own_call(self):
        self.login()
        self.post("ar")
        self.post("en")
        self.assertEqual(self.calls(), 2)
        record_customer_payment("CP-Y2", self.yesterday, make_customer(), self.cashbox, D("1.00"), user=self.owner)
        self.assertIn("76.25", self.post("ar").json()["text"])  # a figure changed: a new text, voiced once more
        self.assertEqual((self.calls(), VoiceSummaryAudio.objects.count()), (3, 3))

    def test_the_text_is_never_written_to_the_audit(self):
        self.login()
        self.post()
        rows = AuditLog.objects.filter(module="ai")
        self.assertEqual(rows.count(), 2)  # started and ok
        for row in rows:
            dump = f"{row.before_data} {row.after_data} {row.reason}"
            for secret in ("250.50", "75.25", "ملخص", "summary."):
                self.assertNotIn(secret, dump)
        self.assertEqual(VoiceSummaryAudio.objects.get().text, AR_TEXT)  # kept with the audio, for the card

    def test_the_post_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(self.post(client=client).status_code, 403)
        self.assertEqual(self.calls(), 0)
        page = client.get("/dashboard/?lang=ar")
        token = page.context["csrf_token"]
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        response = client.post(ENDPOINT, {"lang": "ar", "csrfmiddlewaretoken": str(token)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls(), 1)

    def test_a_claim_in_progress_is_not_paid_twice_and_a_stale_one_is_taken_over(self):
        summary = build_summary(self.owner, "ar")
        claim = VoiceSummaryAudio.objects.create(scope_key="group", language="ar", day=self.today, text_sha256=summary.text_sha256, text=summary.text)
        self.login()
        busy = self.post()
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json()["error"], "صوت الملخص بيتجهّز دلوقتي. جرّب تاني كمان شوية.")
        self.assertEqual(self.calls(), 0)
        VoiceSummaryAudio.objects.filter(pk=claim.pk).update(created_at=timezone.now() - voice_summary.STALE_CLAIM - timedelta(seconds=1))
        self.assertTrue(self.post().json()["ok"])
        self.assertEqual((self.calls(), VoiceSummaryAudio.objects.get().ready), (1, True))

    def test_voicing_inside_a_transaction_is_refused_and_leaves_no_claim(self):
        summary = build_summary(self.owner, "ar")
        with self.assertRaises(services.AiCallInTransaction), transaction.atomic():
            voice_summary.speak(summary, self.owner)
        self.assertEqual((self.calls(), VoiceSummaryAudio.objects.count()), (0, 0))


class AudioEndpointTests(VoiceSummaryBase):
    def voiced(self):
        self.login()
        return self.post().json()["audio_url"]

    def test_the_audio_is_streamed_privately_with_its_type(self):
        url = self.voiced()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "audio/mpeg")
        self.assertEqual(response.content, SILENT_MP3)
        self.assertEqual(response["Content-Length"], str(len(SILENT_MP3)))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        cache = response["Cache-Control"]
        self.assertIn("private", cache)
        self.assertIn("no-store", cache)
        self.assertEqual(self.calls(), 1)  # streaming never calls the provider

    def test_a_listener_with_other_permissions_cannot_fetch_it(self):
        url = self.voiced()
        self.login(person(RoleCode.STOCK_KEEPER, "voice_keeper_audio"))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.login(nobody("voice_nobody_audio"))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_wrong_language_wrong_digest_off_or_signed_out_is_refused(self):
        url = self.voiced()
        self.assertEqual(self.client.get(url.replace("lang=ar", "lang=en")).status_code, 404)  # not voiced in English
        self.assertEqual(self.client.get(f"/ai/voice-summary/audio/{'0' * 64}.mp3?lang=ar").status_code, 404)
        self.switch(False)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.switch(True)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # to the login page

    def test_audio_past_its_day_is_not_served(self):
        url = self.voiced()
        self.assertEqual(self.client.get(url).status_code, 200)
        VoiceSummaryAudio.objects.update(day=self.yesterday)  # the same text, voiced on another Cairo day
        self.assertEqual(self.client.get(url).status_code, 404)


    def test_a_cached_url_stops_working_once_the_text_changes(self):
        url = self.voiced()
        record_customer_payment("CP-Y3", self.yesterday, make_customer(), self.cashbox, D("1.00"), user=self.owner)
        self.assertEqual(self.client.get(url).status_code, 404)  # the card then POSTs again and voices the new text
        fresh = self.post().json()
        self.assertIn("76.25", fresh["text"])
        self.assertNotEqual(fresh["audio_url"], url)
        self.assertEqual(self.client.get(fresh["audio_url"]).status_code, 200)


class CardTests(VoiceSummaryBase):
    def test_the_card_is_hidden_while_the_capability_is_off(self):
        self.switch(False)
        self.login()
        for lang in ("ar", "en"):
            page = self.client.get(f"/dashboard/?lang={lang}")
            self.assertEqual(page.status_code, 200)
            self.assertIsNone(page.context["voice_summary"])
            self.assertNotContains(page, "data-voice-summary")
            self.assertNotContains(page, "voice_summary.js")
        self.assertEqual(self.client.get(ENDPOINT).status_code, 404)
        self.assertEqual(self.calls(), 0)

    def test_the_card_in_arabic(self):
        self.login()
        page = self.client.get("/dashboard/?lang=ar")
        self.assertContains(page, "data-voice-summary")
        self.assertContains(page, "ملخص امبارح")
        self.assertContains(page, f"«{AR_TEXT}»")
        self.assertContains(page, "الأرقام من تقارير حسبة نفسها. الصوت بس اللي متولّد.")
        self.assertContains(page, 'aria-label="شغّل الملخص"')
        self.assertContains(page, 'data-voice-figure="sales"')
        self.assertContains(page, 'data-voice-figure="low_stock"')
        self.assertContains(page, "voice_summary.js")
        self.assertContains(page, 'data-audio-url=""')  # nothing voiced yet: the first press POSTs
        self.assertEqual(self.calls(), 0)

    def test_the_card_in_english(self):
        self.login()
        page = self.client.get("/dashboard/?lang=en")
        self.assertContains(page, "Yesterday&#x27;s summary")
        self.assertContains(page, EN_TEXT.replace("'", "&#x27;"))
        self.assertContains(page, "The figures come from Hesba&#x27;s own reports. Only the voice is generated.")
        self.assertContains(page, 'aria-label="Play the summary"')
        self.assertContains(page, 'data-lang="en"')


class RefusalTests(VoiceSummaryBase):
    def refused(self, lang="ar"):
        self.login()
        response = self.post(lang)
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["audio_url"], "")
        self.assertEqual(data["text"], AR_TEXT if lang == "ar" else EN_TEXT)  # the card keeps showing the text
        self.assertEqual(VoiceSummaryAudio.objects.count(), 0)  # the claim is gone, so the next press can try again
        return data["error"]

    def test_the_kill_switch(self):
        services.set_disabled(True)
        self.assertEqual(self.refused("ar"), "مزايا الذكاء الاصطناعي متوقفة للشركة دي.")
        self.assertEqual(self.refused("en"), "AI features are switched off for this company.")

    def test_the_daily_cap(self):
        services.set_daily_cap(0)
        self.assertEqual(self.refused("ar"), "وصلت لحد مكالمات الذكاء الاصطناعي النهارده (0 مكالمة). جرّب تاني بكرة.")
        self.assertEqual(self.refused("en"), "Today's AI call limit is reached (0 calls). Try again tomorrow.")

    def test_not_connected(self):
        with mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: lambda: None}):
            self.assertEqual(self.refused("ar"), "خدمة الذكاء الاصطناعي مش موصّلة على السيرفر ده.")

    def test_the_provider_failing(self):
        self.provider.fail_with = "boom"
        self.assertEqual(self.refused("ar"), "خدمة الذكاء الاصطناعي مردّتش. جرّب تاني بعد شوية.")
        self.provider.fail_with = None
        self.assertTrue(self.post().json()["ok"])  # the next press works

    def test_the_feature_switched_off_after_the_page_loaded(self):
        self.login()
        self.switch(False)
        response = self.post()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "ميزة الذكاء الاصطناعي دي مش متشغّلة.")
        self.assertEqual(self.calls(), 0)

    def test_refusals_without_a_request(self):
        services.set_disabled(True)
        with self.assertRaisesMessage(ValidationError, "AI features are switched off for this company."):
            voice_summary.speak(build_summary(self.owner, "ar"), self.owner)


class PruneTests(VoiceSummaryBase):
    def make(self, day, text="x"):
        return VoiceSummaryAudio.objects.create(scope_key="group", language="ar", day=day, text_sha256=text * 64, text=text, ready=True)

    def test_seven_cairo_days_are_kept(self):
        kept = self.make(self.today - timedelta(days=6), "a")
        gone = self.make(self.today - timedelta(days=7), "b")
        self.assertEqual(voice_summary.prune(self.today), 1)
        self.assertTrue(VoiceSummaryAudio.objects.filter(pk=kept.pk).exists())
        self.assertFalse(VoiceSummaryAudio.objects.filter(pk=gone.pk).exists())

    def test_voicing_prunes_on_access_and_the_command_prunes_too(self):
        self.make(self.today - timedelta(days=30), "c")
        self.login()
        self.post()
        self.assertEqual(VoiceSummaryAudio.objects.count(), 1)
        self.make(self.today - timedelta(days=8), "d")
        out = StringIO()
        call_command("prune_voice_summaries", stdout=out)
        self.assertIn("Deleted 1", out.getvalue())
        self.assertEqual(VoiceSummaryAudio.objects.count(), 1)


class NoNetworkBaseTests(AiTestCase):
    def test_every_test_case_in_this_module_uses_the_no_network_base(self):
        import inspect
        import sys
        import unittest

        from .no_network import NoNetworkMixin

        cases = [obj for obj in vars(sys.modules[__name__]).values() if inspect.isclass(obj) and issubclass(obj, unittest.TestCase)]
        self.assertGreater(len(cases), 5)
        self.assertEqual([case.__name__ for case in cases if not issubclass(case, NoNetworkMixin)], [])


# ---- A1 independent review (APPROVE WITH FIXES): the fixes and the missing tests ----

AUDIO_COLUMN = '"ai_voicesummaryaudio"."audio"'


def queried(run):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        result = run()
    return result, [query["sql"] for query in queries]


class AudioBytesTests(VoiceSummaryBase):
    """Review 1: the MP3 is read only to stream it, never to decide whether a URL exists."""

    def test_the_card_the_lookup_and_the_json_never_read_the_audio_column(self):
        self.login()
        self.post()
        summary = build_summary(self.owner, "ar")
        context, sql = queried(lambda: voice_summary.card_context(self.owner, "ar"))
        self.assertTrue(context["audio_url"])
        self.assertTrue(any("ai_voicesummaryaudio" in query for query in sql))
        self.assertFalse(any(AUDIO_COLUMN in query for query in sql))
        row, sql = queried(lambda: voice_summary.cached(summary))
        self.assertTrue(row.ready)
        self.assertFalse(any(AUDIO_COLUMN in query for query in sql))
        for method in (self.client.get, self.client.post):
            response, sql = queried(lambda: method(ENDPOINT, {"lang": "ar"}))
            self.assertTrue(response.json()["audio_url"])
            self.assertFalse(any(AUDIO_COLUMN in query for query in sql), method)
        response, sql = queried(lambda: self.client.get(response.json()["audio_url"]))
        self.assertEqual(response.content, SILENT_MP3)
        self.assertTrue(any(AUDIO_COLUMN in query for query in sql))  # streaming is the one place that reads it

    def test_voicing_does_not_read_the_bytes_back(self):
        _, sql = queried(lambda: voice_summary.speak(build_summary(self.owner, "ar"), self.owner))
        self.assertFalse(any(query.lstrip().upper().startswith("SELECT") and AUDIO_COLUMN in query for query in sql))


class UserCapTests(VoiceSummaryBase):
    """Review 3: a per-person daily limit on voicings; cached audio stays free."""

    def test_the_limit_counts_voicings_only(self):
        from django.test import override_settings

        self.login()
        with override_settings(AI_VOICE_SUMMARY_USER_DAILY_CAP=1):
            first = self.post().json()
            self.assertTrue(first["ok"])
            self.assertTrue(self.post().json()["ok"])  # the same text again: cached, free, not counted
            self.assertEqual(self.client.get(first["audio_url"]).status_code, 200)
            self.assertEqual((self.calls(), voice_summary.voicings_today(self.owner)), (1, 1))
            record_customer_payment("CP-Y4", self.yesterday, make_customer(), self.cashbox, D("1.00"), user=self.owner)
            refused = self.post()
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["error"], "وصلت لحد تشغيل ملخص الصوت النهارده (1 مرات للشخص). جرّب تاني بكرة.")
            self.assertEqual(self.post("en").json()["error"], "You have reached today's voice summary limit (1 per person). Try again tomorrow.")
            self.assertEqual(self.calls(), 1)
            self.assertFalse(VoiceSummaryAudio.objects.filter(ready=False).exists())  # no claim left behind
            other = person(RoleCode.OWNER, "voice_owner_two")
            self.login(other)
            self.assertTrue(self.post().json()["ok"])  # the limit is per person
            self.assertEqual(self.calls(), 2)

    def test_the_default_is_five_and_only_this_feature_s_started_rows_count(self):
        self.assertEqual(voice_summary.user_daily_cap(), 5)
        self.login()
        self.post()
        AuditLog.objects.create(event_type="export", actor=self.owner, module="ai", action=services.CALL_STARTED,
                                after_data={"feature": "another_feature"}, reason="x")
        self.assertEqual(voice_summary.voicings_today(self.owner), 1)


class SaveTests(VoiceSummaryBase):
    """Review 4: the save after a paid call."""

    def test_the_content_type_is_cut_to_fit(self):
        from .providers.base import Speech

        for given, stored in (("audio/mpeg; charset=binary", "audio/mpeg"), ("audio/" + "x" * 80, "audio/mpeg"),
                              ("text/html", "audio/mpeg"), ("audio/ogg", "audio/ogg"), ("", "audio/mpeg")):
            VoiceSummaryAudio.objects.all().delete()
            with self.subTest(given=given), mock.patch.object(self.provider, "synthesize", return_value=Speech(audio=SILENT_MP3, content_type=given)):
                row = voice_summary.speak(build_summary(self.owner, "ar"), self.owner)
                self.assertEqual(VoiceSummaryAudio.objects.get(pk=row.pk).content_type, stored)

    def test_a_failed_save_frees_the_claim_at_once_and_is_logged(self):
        from django.db import DatabaseError
        from django.db.models.query import QuerySet

        real_update = QuerySet.update

        def update(queryset, **kwargs):
            if "audio" in kwargs:
                raise DatabaseError("disk full")
            return real_update(queryset, **kwargs)

        with mock.patch.object(QuerySet, "update", update), self.assertLogs("ai.voice_summary", "ERROR") as logs, \
                self.assertRaises(DatabaseError):
            voice_summary.speak(build_summary(self.owner, "ar"), self.owner)
        self.assertIn("could not be saved", logs.output[0])
        self.assertEqual(VoiceSummaryAudio.objects.count(), 0)  # no "being prepared" for the next press
        self.assertEqual(AuditLog.objects.filter(module="ai", action=services.CALL_OK).count(), 1)  # the paid call is on record
        self.assertTrue(voice_summary.speak(build_summary(self.owner, "ar"), self.owner).ready)


class DashboardSafetyTests(VoiceSummaryBase):
    def test_a_failing_card_never_breaks_the_dashboard(self):
        self.login()
        with mock.patch("ai.summary.build_summary", side_effect=RuntimeError("boom")), \
                self.assertLogs("reports.dashboard_views", "ERROR"):
            page = self.client.get("/dashboard/?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context["voice_summary"])
        self.assertNotContains(page, "data-voice-summary")
        self.assertContains(page, 'data-kpi="sales_today"')  # the rest of the dashboard is there


class TileLinkTests(VoiceSummaryBase):
    def test_the_collections_tile_says_it_opens_the_whole_list(self):
        tiles = {tile["key"]: tile for tile in build_summary(self.owner, "ar").tiles}
        self.assertEqual(tiles["receipts"]["sub"], "افتح قايمة التحصيلات كلها")
        tiles = {tile["key"]: tile for tile in build_summary(self.owner, "en").tiles}
        self.assertEqual(tiles["receipts"]["sub"], "Open the full collections list")


class BranchTests(VoiceSummaryBase):
    """Review 8: the branches the first round did not reach."""

    def role_with(self, username, *codes):
        from hesba_testing.factories import grant
        from permissions.models import Permission

        role = make_role(code=f"R-{username}")
        for code in codes:
            grant(role, Permission.objects.get(code=code))
        user = make_user(username=username)
        make_user_profile(user=user, role=role)
        return user

    def test_the_invoice_count_without_the_sales_total(self):
        everyone = self.role_with("voice_counter", "sales.view_sales_invoices", "reports.view_all_sales_report")
        summary = build_summary(everyone, "ar")
        self.assertEqual(summary.text, "أهلاً، ده ملخص النهارده. امبارح اتعمل 2 فاتورة بيع.")
        self.assertEqual([(tile["key"], tile["label"], tile["value"], tile["url"]) for tile in summary.tiles],
                         [("invoices", "فواتير امبارح", "2", "")])  # no link: the sales report needs a permission they lack
        self.assertEqual(build_summary(everyone, "en").text, "Hello, here is today's summary. There were 2 sales invoices yesterday.")
        mine = self.role_with("voice_counter_own", "sales.view_sales_invoices")
        summary = build_summary(mine, "ar")
        self.assertEqual(summary.text, "أهلاً، ده ملخص النهارده. امبارح عملت 0 فاتورة بيع.")  # their own: none
        self.assertEqual(summary.tiles[0]["label"], "فواتيري امبارح")

    def test_collections_are_the_shop_s_as_on_the_dashboard_card(self):
        # receipts_today has no scope permission, so a cashier's card (and summary) shows the whole shop's collections.
        cashier = person(RoleCode.CASHIER, "voice_cashier_receipts")
        record_customer_payment("CP-C1", self.yesterday, make_customer(), self.cashbox, D("3.00"), user=cashier)
        self.assertEqual(DashboardFigures(cashier, self.yesterday).value_for("receipts_today", "all"), D("78.25"))
        self.assertIn("والتحصيل كان 78.25 جنيه مصري.", build_summary(cashier, "ar").text)

    def test_a_claim_deleted_in_between_is_claimed_again(self):
        from django.db import IntegrityError

        real_create = VoiceSummaryAudio.objects.create
        attempts = []

        def create(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise IntegrityError("another request held it, then gave up")
            return real_create(**kwargs)

        with mock.patch.object(VoiceSummaryAudio.objects, "create", side_effect=create):
            row = voice_summary.speak(build_summary(self.owner, "ar"), self.owner)
        self.assertEqual((len(attempts), row.ready, self.calls()), (2, True, 1))

    def test_a_row_made_ready_in_between_is_returned_unpaid(self):
        summary = build_summary(self.owner, "ar")
        ready = VoiceSummaryAudio.objects.create(scope_key="group", language="ar", day=self.today, text_sha256=summary.text_sha256,
                                                 text=summary.text, ready=True, audio=SILENT_MP3, content_type="audio/mpeg", size=len(SILENT_MP3))
        self.assertEqual(voice_summary.speak(summary, self.owner).pk, ready.pk)
        self.assertEqual((self.calls(), AuditLog.objects.filter(module="ai").count()), (0, 0))

    def test_any_other_exception_deletes_the_claim(self):
        with mock.patch.object(self.provider, "synthesize", side_effect=RuntimeError("unexpected")), self.assertRaises(RuntimeError):
            voice_summary.speak(build_summary(self.owner, "ar"), self.owner)
        self.assertEqual(VoiceSummaryAudio.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(module="ai", action=services.CALL_ERROR).count(), 1)

    def test_counting_words(self):
        from .summary import _ar_count, _en_count

        self.assertEqual([_ar_count(n, "صنف", "أصناف") for n in (1, 2, 3, 10, 11)], ["1 صنف", "2 صنف", "3 أصناف", "10 أصناف", "11 صنف"])
        self.assertEqual([_en_count(n, "item") for n in (0, 1, 2)], ["0 items", "1 item", "2 items"])

    def test_a_page_left_open_across_cairo_midnight(self):
        before = datetime.combine(self.yesterday, dt_time(23, 59), tzinfo=CAIRO)
        after = datetime.combine(self.today, dt_time(0, 1), tzinfo=CAIRO)
        self.login()
        with mock.patch("django.utils.timezone.now", return_value=before):
            old = self.post().json()
            self.assertTrue(old["ok"])
            self.assertEqual(self.client.get(old["audio_url"]).status_code, 200)
        with mock.patch("django.utils.timezone.now", return_value=after):
            self.assertEqual(self.client.get(old["audio_url"]).status_code, 404)  # yesterday's audio on today's page: the card POSTs again
            new = self.post().json()
            self.assertEqual(new["text"], AR_TEXT)
            self.assertNotEqual(new["audio_url"], old["audio_url"])
            self.assertEqual(self.client.get(new["audio_url"]).status_code, 200)
        self.assertEqual(sorted(VoiceSummaryAudio.objects.values_list("day", flat=True)), [self.yesterday, self.today])
        self.assertEqual(self.calls(), 2)

