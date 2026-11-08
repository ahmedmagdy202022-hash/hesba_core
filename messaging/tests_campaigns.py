"""S8 (services activity 17): loyalty and marketing campaigns put on the outbox one message per customer."""

from collections import Counter
from datetime import timedelta
from unittest import mock
from urllib.parse import unquote

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from loyalty.models import EntryKind, PointsEntry
from master_data.models import Category
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine

from . import campaigns, services, whatsapp
from .models import Campaign, CampaignAudience, CampaignStatus, MessageKind, MessageTemplate, OutboxMessage, OutboxStatus

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def ago(days):
    return TODAY - timedelta(days=days)


class CampaignSetup(TestCase):
    """Customers with known purchases and points, so every audience has an exact count.

    ====  =========  ==========  ===========================  ======
    id    phone      points      posted purchases             note
    ====  =========  ==========  ===========================  ======
    mona  yes        150         Hair 10 days ago
    hala  yes        40          Hair 100 d ago, Nails 20 d
    sara  yes        500         Nails 50 days ago
    none  NO         200         Hair 100 days ago
    gone  yes        -           -                            inactive customer
    new   yes        -           -                            never bought
    twin  = mona's   -           -                            same number as mona
    ====  =========  ==========  ===========================  ======
    """

    def setUp(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        prepared_client("commercial", "wholesale")
        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", True)
        self.owner = person(RoleCode.OWNER, "cp_owner")
        self.cashier = person(RoleCode.CASHIER, "cp_cashier")
        self.keeper = person(RoleCode.STOCK_KEEPER, "cp_keeper")
        self.location = make_location(location_code="CP-MAIN")
        self.hair = Category.objects.create(category_code="HAIR", name_ar="شعر", name_en="Hair")
        self.nails = Category.objects.create(category_code="NAILS", name_ar="أظافر", name_en="Nails")
        self.dye = make_item(item_code="DYE", item_name="صبغة", category=self.hair)
        self.polish = make_item(item_code="POLISH", item_name="مانيكير", category=self.nails)
        self.mona = make_customer(customer_code="CP-MONA", name="منى", phone="01001234567")
        self.hala = make_customer(customer_code="CP-HALA", name="هالة", phone="01111111111")
        self.sara = make_customer(customer_code="CP-SARA", name="سارة", whatsapp="01222222222")
        self.none = make_customer(customer_code="CP-NONE", name="من غير رقم", phone="")
        self.gone = make_customer(customer_code="CP-GONE", name="موقوف", phone="01333333333", active=False)
        self.new = make_customer(customer_code="CP-NEW", name="جديد", phone="01555555555")
        self.twin = make_customer(customer_code="CP-TWIN", name="منى التانية", phone="01001234567")
        make_customer(customer_code="WALK-IN", name="عميل نقدي", phone="01999999999")
        self.invoices = 0
        self.buy(self.mona, self.dye, 10)
        self.buy(self.hala, self.dye, 100)
        self.buy(self.hala, self.polish, 20)
        self.buy(self.sara, self.polish, 50)
        self.buy(self.none, self.dye, 100)
        self.buy(self.gone, self.dye, 100)
        self.buy(self.new, self.dye, 5, status=SalesInvoiceStatus.CANCELLED)  # a cancelled sale is not a purchase
        self.buy(self.twin, self.dye, 5, status=SalesInvoiceStatus.DRAFT)  # nor is a draft
        self.earn(self.mona, 150)
        self.earn(self.hala, 40)
        self.earn(self.sara, 500)
        self.earn(self.none, 200)
        Campaign.objects.all().delete()

    def buy(self, customer, item, days, status=SalesInvoiceStatus.POSTED, location=None):
        self.invoices += 1
        invoice = SalesInvoice.objects.create(invoice_number=f"CP-{self.invoices}", invoice_date=ago(days), customer=customer, selling_location=location or self.location, status=status)
        SalesLine.objects.create(invoice=invoice, line_number=1, item=item, quantity=1, unit_sale_price=10, line_total_amount=10)
        return invoice

    def earn(self, customer, points, expires_on=None):
        return PointsEntry.objects.create(customer=customer, kind=EntryKind.EARN, points=points, entry_date=ago(1), expires_on=expires_on)

    def campaign(self, audience=CampaignAudience.ALL, threshold=0, category=None, text="أهلاً {name} من {company}", **extra):
        from entities.services import main_entity

        fields = {"name": "حملة", "text": text, "audience": audience, "threshold": threshold, "category": category, "scheduled_day": TODAY, "entity": main_entity(), "created_by": self.owner}
        fields.update(extra)
        return Campaign.objects.create(**fields)

    def form(self, **extra):
        data = {"lang": "ar", "name": "وحشتينا", "text": "أهلاً {name}، عندك {points} نقطة", "audience": "all", "threshold": "", "category": "", "scheduled_day": TODAY.isoformat(), "action": "save"}
        data.update(extra)
        return data

    def count(self, **fields):
        result = campaigns.audience(self.campaign(**fields), TODAY)
        return result.match, result.no_phone, result.duplicates, result.count


class AudienceTests(CampaignSetup):
    def test_every_customer_with_a_valid_phone(self):
        # 6 match (the inactive customer and the walk-in are never in): no number 1, mona's twin shares her number 1, so 4 are reached
        self.assertEqual(self.count(), (6, 1, 1, 4))
        names = [row[1] for row in campaigns.audience(self.campaign(), TODAY).rows]
        self.assertEqual(names, ["منى", "هالة", "سارة", "جديد"])

    def test_points_at_least_n_uses_the_spendable_balance(self):
        self.assertEqual(self.count(audience="points", threshold=100), (3, 1, 0, 2))  # mona 150, sara 500, and 'none' 200 has no number
        self.assertEqual(self.count(audience="points", threshold=300), (1, 0, 0, 1))  # sara
        self.assertEqual(self.count(audience="points", threshold=600), (0, 0, 0, 0))
        self.earn(self.hala, 1000, expires_on=ago(1))  # expired points never count, though the ledger sums to 1040
        self.assertEqual(self.count(audience="points", threshold=600), (0, 0, 0, 0))
        self.assertEqual(self.count(audience="points", threshold=40)[3], 3)  # mona, hala (40) and sara; 'none' has no number

    def test_a_draft_holding_points_lowers_what_counts(self):
        from loyalty.models import InvoiceRedemption

        draft = self.buy(self.mona, self.dye, 0, status=SalesInvoiceStatus.DRAFT)
        InvoiceRedemption.objects.create(invoice=draft, points=100, amount=10)  # 100 of mona's 150 are set aside
        self.assertEqual(self.count(audience="points", threshold=100)[3], 1)  # only sara

    def test_no_purchase_in_the_last_n_days(self):
        self.assertEqual(self.count(audience="inactive", threshold=30), (2, 1, 0, 1))  # sara (50 d) and 'none' (100 d, no number); mona 10 d and hala 20 d are recent
        self.assertEqual(self.count(audience="inactive", threshold=15), (3, 1, 0, 2))  # hala (20 d) is now out of the window too
        self.assertEqual(self.count(audience="inactive", threshold=5), (4, 1, 0, 3))  # and mona (10 d); never-bought, cancelled and draft sales never count

    def test_the_day_boundary_is_exact(self):
        self.assertEqual(self.count(audience="inactive", threshold=50)[3], 1)  # sara's last purchase was exactly 50 days ago: 50 days have passed
        self.assertEqual(self.count(audience="inactive", threshold=51)[3], 0)  # 51 days have not
        self.assertEqual(self.count(audience="inactive", threshold=21)[3], 1)  # hala (20 d) is within 21 days, sara (50 d) is not

    def test_bought_the_category_recently_or_not(self):
        self.assertEqual(self.count(audience="category", threshold=30, category=self.hair), (1, 0, 0, 1))  # mona only
        self.assertEqual(self.count(audience="category", threshold=120, category=self.hair), (3, 1, 0, 2))  # mona, hala, none (no number)
        self.assertEqual(self.count(audience="category", threshold=30, category=self.nails), (1, 0, 0, 1))  # hala, 20 days ago
        self.assertEqual(self.count(audience="lapsed", threshold=30, category=self.hair), (2, 1, 0, 1))  # hala (100 d) and none (no number); mona is recent
        self.assertEqual(self.count(audience="lapsed", threshold=30, category=self.nails), (1, 0, 0, 1))  # sara, 50 days ago

    def test_a_category_includes_its_sub_categories(self):
        gel = Category.objects.create(category_code="GEL", name_ar="جل", parent=self.nails)
        self.buy(self.new, make_item(item_code="GEL1", item_name="جل", category=gel), 3)
        self.assertEqual(self.count(audience="category", threshold=30, category=self.nails)[3], 2)  # hala and the new customer

    def test_purchases_are_read_in_the_campaigns_entity_only(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E-CP", name_ar="فرع")
        branch_store = make_location(location_code="CP-BRANCH", entity=branch)
        self.buy(self.new, self.dye, 90, location=branch_store)  # bought at the branch only
        self.assertEqual(self.count(audience="inactive", threshold=30), (2, 1, 0, 1))  # main: sara and 'none'; the branch sale of 'new' is not main's
        branch_campaign = self.campaign(audience="inactive", threshold=30, entity=branch)
        self.assertEqual(campaigns.audience(branch_campaign, TODAY).count, 1)  # the branch's own customer: 'new'


class QueueTests(CampaignSetup):
    def queue(self, campaign, **kwargs):
        with mock.patch.object(whatsapp, "_http") as http, mock.patch.object(whatsapp, "send") as send:
            result = campaigns.queue(campaign, kwargs.pop("user", self.owner), **kwargs)
        http.assert_not_called()  # queuing never calls WhatsApp
        send.assert_not_called()
        return result

    def test_one_message_per_customer_with_the_placeholders_rendered(self):
        campaign = self.campaign(text="أهلاً {name}، عندك {points} نقطة في {company}، آخر شراء من {days} يوم")
        result = self.queue(campaign)
        self.assertEqual(result["count"], 4)
        rows = {row.name: row for row in OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN)}
        self.assertEqual(sorted(rows), ["جديد", "سارة", "منى", "هالة"])
        mona = rows["منى"]
        self.assertEqual((mona.phone, mona.status, mona.day, mona.key, mona.link), ("201001234567", OutboxStatus.PENDING, TODAY, f"campaign:{campaign.pk}:{self.mona.pk}", f"/parties/customer/{self.mona.pk}/"))
        self.assertIn("150 نقطة", mona.text)
        self.assertIn("من 10 يوم", mona.text)
        self.assertIn("Demo Store", mona.text)
        self.assertIn("500 نقطة", rows["سارة"].text)
        self.assertIn("من 50 يوم", rows["سارة"].text)
        self.assertIn("0 نقطة", rows["جديد"].text)  # never bought and no points: the numbers are zero and blank, never the raw placeholder
        self.assertNotIn("{", "".join(row.text for row in rows.values()))
        self.assertTrue(all(row.entity.is_main for row in rows.values()))
        campaign.refresh_from_db()
        self.assertEqual((campaign.status, campaign.queued_count, campaign.queued_by), (CampaignStatus.QUEUED, 4, self.owner))

    def test_the_audit_log_records_the_queue(self):
        campaign = self.campaign(audience="points", threshold=100)
        self.queue(campaign)
        entry = AuditLog.objects.get(action="queue_campaign")
        self.assertEqual((entry.module, entry.actor, entry.object_id), ("messaging", self.owner, str(campaign.pk)))
        self.assertEqual(entry.after_data["messages"], 2)
        self.assertEqual(entry.after_data["excluded_no_phone"], 1)

    def test_a_campaign_never_queues_twice_for_a_customer(self):
        campaign = self.campaign()
        self.queue(campaign)
        with self.assertRaises(ValidationError):
            self.queue(campaign)  # already queued
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).count(), 4)
        Campaign.objects.filter(pk=campaign.pk).update(status=CampaignStatus.DRAFT)  # even if it were queued again by mistake
        self.assertEqual(self.queue(campaign)["count"], 0)
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).count(), 4)
        other = self.campaign(name="تانية")  # a different campaign is a different message
        self.queue(other)
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).count(), 8)

    def test_nothing_is_queued_when_the_kind_is_switched_off_or_nobody_matches(self):
        campaigns.save_settings({"active": "", "wa_template": ""}, self.owner)
        campaign = self.campaign()
        with self.assertRaises(ValidationError):
            self.queue(campaign)
        self.assertFalse(OutboxMessage.objects.exists())
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.DRAFT)
        campaigns.save_settings({"active": "1", "wa_template": ""}, self.owner)
        empty = self.campaign(audience="points", threshold=9999)
        with self.assertRaises(ValidationError):
            self.queue(empty)
        self.assertFalse(OutboxMessage.objects.exists())

    def test_switching_the_kind_off_withdraws_what_still_waits(self):
        self.queue(self.campaign())
        campaigns.save_settings({"active": "", "wa_template": ""}, self.owner)
        services.build()
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, status=OutboxStatus.WITHDRAWN).count(), 4)
        self.assertFalse(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN, status=OutboxStatus.PENDING).exists())
        self.assertEqual(set(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("text", flat=True)), {""})  # kept as the once-only record

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=3)
    def test_the_daily_cap_spreads_the_rest_over_the_next_days_and_is_shared(self):
        first = self.campaign()
        result = self.queue(first)
        self.assertEqual(result["days"], [(TODAY, 3), (TODAY + timedelta(days=1), 1)])
        per_day = Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True))
        self.assertEqual(per_day, {TODAY: 3, TODAY + timedelta(days=1): 1})
        second = self.campaign(name="تانية")
        self.assertEqual(self.queue(second)["days"], [(TODAY + timedelta(days=1), 2), (TODAY + timedelta(days=2), 2)])  # today is full
        per_day = Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True))
        self.assertTrue(all(n <= 3 for n in per_day.values()), per_day)
        self.assertEqual(sum(per_day.values()), 8)

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=3)
    def test_messages_left_waiting_move_forward_without_breaking_the_cap(self):
        self.queue(self.campaign())
        tomorrow = TODAY + timedelta(days=1)
        services.build(tomorrow)  # nobody sent today's three
        per_day = Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True))
        self.assertEqual(per_day, {tomorrow: 3, tomorrow + timedelta(days=1): 1})
        services.build(tomorrow)  # again the same day: nothing moves
        self.assertEqual(Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True)), per_day)

    def test_the_default_cap_is_fifty_and_a_later_start_waits_for_its_day(self):
        self.assertEqual(campaigns.daily_cap(), 50)
        later = TODAY + timedelta(days=3)
        self.queue(self.campaign(scheduled_day=later))
        self.assertEqual(set(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True)), {later})
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertNotContains(page, 'data-kind="campaign"')  # not before its day
        services.build(later)
        self.assertEqual(services.counts(self.owner, later)["pending"], 4)

    def test_a_campaign_started_in_the_past_starts_today(self):
        campaign = self.campaign(scheduled_day=ago(5))
        self.assertEqual(self.queue(campaign)["days"], [(TODAY, 4)])

    def test_the_outbox_sends_a_campaign_message_with_a_click_and_nothing_else_does(self):
        self.queue(self.campaign(text="أهلاً {name}"))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertContains(page, 'data-kind="campaign"')
        self.assertContains(page, "حملة")
        self.assertNotContains(page, 'data-template="campaign"')  # a campaign's words are its own, not an outbox template
        message = OutboxMessage.objects.get(kind=MessageKind.CAMPAIGN, name="منى")
        response = self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send", "message": message.pk})
        self.assertTrue(response["Location"].startswith("https://wa.me/201001234567?text="))
        self.assertIn("منى", unquote(response["Location"]))
        message.refresh_from_db()
        self.assertEqual((message.status, message.channel), (OutboxStatus.SENT, "link"))

    def test_status_becomes_done_when_nothing_waits(self):
        campaign = self.campaign()
        self.queue(campaign)
        for message in OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN):
            services.mark(message, OutboxStatus.SKIPPED if message.name == "جديد" else OutboxStatus.SENT, self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("messaging:campaigns") + "?lang=en"), 'data-status="done"')
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, CampaignStatus.DONE)

    def test_the_api_sends_only_today_and_only_with_an_approved_template(self):
        campaigns.save_settings({"active": "1", "wa_template": "hesba_campaign"}, self.owner)
        self.queue(self.campaign(scheduled_day=TODAY + timedelta(days=1)))
        with mock.patch.object(whatsapp, "missing", return_value=[]), mock.patch.object(whatsapp, "send", return_value=("wamid.1", "")) as send:
            self.assertEqual(services.send_by_api(self.owner), {"sent": 0, "failed": 0, "left": 0})  # all four are for tomorrow
            send.assert_not_called()


class PermissionAndEntityTests(CampaignSetup):
    def test_a_viewer_reads_but_cannot_create_save_or_queue(self):
        draft = self.campaign()
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("messaging:campaigns") + "?lang=ar").status_code, 200)
        self.assertNotContains(self.client.get(reverse("messaging:campaigns") + "?lang=ar"), "data-new-campaign")
        self.assertNotContains(self.client.get(reverse("messaging:campaigns") + "?lang=ar"), "data-campaign-settings")
        self.assertEqual(self.client.get(reverse("messaging:campaign_new")).status_code, 403)
        self.assertEqual(self.client.post(reverse("messaging:campaign_new"), self.form()).status_code, 403)
        self.assertEqual(self.client.get(reverse("messaging:campaign", args=[draft.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("messaging:campaign", args=[draft.pk]), self.form(action="queue")).status_code, 403)
        self.assertEqual(self.client.post(reverse("messaging:campaign_delete", args=[draft.pk]), {"lang": "ar"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("messaging:campaigns"), {"action": "settings", "active": "1"}).status_code, 403)
        self.assertEqual(Campaign.objects.count(), 1)
        self.assertFalse(OutboxMessage.objects.exists())
        with self.assertRaises(PermissionDenied):
            campaigns.queue(draft, self.cashier)
        with self.assertRaises(PermissionDenied):
            campaigns.save_draft(self.form(), self.cashier)
        draft.refresh_from_db()
        self.assertEqual(draft.status, CampaignStatus.DRAFT)

    def test_a_viewer_can_open_a_queued_campaign(self):
        campaign = self.campaign()
        campaigns.queue(campaign, self.owner)
        self.client.force_login(self.cashier)
        self.assertContains(self.client.get(reverse("messaging:campaign", args=[campaign.pk]) + "?lang=ar"), "data-schedule")

    def test_someone_without_customer_access_sees_nothing(self):
        self.client.force_login(self.keeper)
        for name in ("messaging:campaigns", "messaging:campaign_new", "messaging:campaign_audience"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_another_entitys_campaign_is_neither_seen_nor_queued(self):
        from entities.models import Entity, EntityMembership
        from entities.services import main_entity

        branch = Entity.objects.create(code="E-CP2", name_ar="فرع تاني")
        outsider = person(RoleCode.MANAGER, "cp_branch")
        from permissions.models import Permission, RolePermission

        RolePermission.objects.update_or_create(role=make_seeded_role(RoleCode.MANAGER), permission=Permission.objects.get(code="settings.manage_settings"), defaults={"allow": True})
        EntityMembership.objects.create(user=outsider, entity=branch, is_default=True)
        mine = self.campaign()
        theirs = self.campaign(name="بتاعة الفرع", entity=branch)
        self.assertEqual(list(campaigns.visible(outsider)), [theirs])
        self.assertEqual(set(campaigns.visible(self.owner)), {mine, theirs})
        with self.assertRaises(PermissionDenied):
            campaigns.queue(mine, outsider)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("messaging:campaign", args=[mine.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("messaging:campaigns") + "?lang=ar"), f'data-campaign="{mine.pk}"')
        self.assertContains(self.client.get(reverse("messaging:campaigns") + "?lang=ar"), f'data-campaign="{theirs.pk}"')
        self.assertEqual(self.client.post(reverse("messaging:campaign_delete", args=[mine.pk]), {"lang": "ar"}).status_code, 403)
        campaigns.queue(theirs, outsider)  # the branch queues its own: the messages carry the branch and the main entity never sees them
        message = OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).first()
        self.assertEqual(message.entity, branch)
        self.assertTrue(services.visible(outsider).filter(pk=message.pk).exists())
        main_person = person(RoleCode.CASHIER, "cp_main")
        EntityMembership.objects.create(user=main_person, entity=main_entity(), is_default=True)
        self.assertFalse(services.visible(main_person).filter(kind=MessageKind.CAMPAIGN).exists())

    def test_a_new_campaign_belongs_to_the_entity_being_worked_in(self):
        from entities.models import Entity
        from entities.services import main_entity

        campaign = campaigns.save_draft(self.form(), self.owner)
        self.assertEqual(campaign.entity, main_entity())
        self.assertEqual(campaign.created_by, self.owner)
        branch = Entity.objects.create(code="E-CP3", name_ar="فرع ثالث")
        from entities.current import working_in

        with working_in(branch):
            self.assertEqual(campaigns.save_draft(self.form(), self.owner).entity, branch)


class FormAndScreenTests(CampaignSetup):
    def test_validation_messages(self):
        for override, fragment in (({"name": " "}, "اسم"), ({"text": ""}, "فاضي"), ({"text": "أهلاً {phone}"}, "{phone}"), ({"audience": "nope"}, "مين"),
                                   ({"audience": "points", "threshold": "0"}, "نقاط"), ({"audience": "inactive", "threshold": "x"}, "الأيام"),
                                   ({"audience": "category", "threshold": "30"}, "الفئة"), ({"scheduled_day": "2026-13-45"}, "اليوم"),
                                   ({"scheduled_day": ago(1).isoformat()}, "عدّى")):
            with self.subTest(override=override):
                with self.assertRaises(ValidationError) as caught:
                    campaigns.save_draft(self.form(**override), self.owner)
                self.assertIn(fragment, " ".join(caught.exception.messages))
        self.assertFalse(Campaign.objects.exists())

    def test_points_need_loyalty_switched_on(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", False)
        with self.assertRaises(ValidationError):
            campaigns.save_draft(self.form(audience="points", threshold="100"), self.owner)

    def test_the_composer_saves_then_queues_after_confirm_in_both_languages(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("messaging:campaign_new") + f"?lang={lang}")
            self.assertContains(page, "data-composer")
            self.assertContains(page, "campaign_composer.js")
            self.assertContains(page, 'data-queue')
        self.assertContains(self.client.get(reverse("messaging:campaign_new") + "?lang=ar"), "حط 4 رسالة في صندوق الرسائل")
        self.assertContains(self.client.get(reverse("messaging:campaign_new") + "?lang=en"), "Put 4 messages on the outbox")
        response = self.client.post(reverse("messaging:campaign_new"), self.form(audience="points", threshold="100", action="save"))
        campaign = Campaign.objects.get()
        self.assertEqual(campaign.status, CampaignStatus.DRAFT)
        self.assertRedirects(response, reverse("messaging:campaign", args=[campaign.pk]) + "?lang=ar", fetch_redirect_response=False)
        draft_page = self.client.get(reverse("messaging:campaign", args=[campaign.pk]) + "?lang=ar")
        self.assertContains(draft_page, "حط 2 رسالة في صندوق الرسائل")
        self.assertContains(draft_page, "عندك 150 نقطة")  # the preview is the first customer's message, placeholders filled
        self.assertContains(draft_page, "data-delete")
        self.client.post(reverse("messaging:campaign", args=[campaign.pk]), self.form(audience="points", threshold="300", action="queue", name="معدّلة"))
        campaign.refresh_from_db()
        self.assertEqual((campaign.name, campaign.status, campaign.queued_count), ("معدّلة", CampaignStatus.QUEUED, 1))
        queued = self.client.get(reverse("messaging:campaign", args=[campaign.pk]) + "?lang=en")
        self.assertContains(queued, "1 messages are on the outbox")
        self.assertContains(queued, "data-schedule")
        self.assertContains(self.client.get(reverse("messaging:campaign", args=[campaign.pk]) + "?lang=ar"), "اتحطت 1 رسالة في صندوق الرسائل")
        again = self.client.post(reverse("messaging:campaign", args=[campaign.pk]), self.form(action="queue"))
        self.assertEqual(again.status_code, 200)  # a queued campaign shows its page; nothing changes
        self.assertEqual(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).count(), 1)

    def test_a_switched_off_kind_keeps_the_draft_and_says_why(self):
        campaigns.save_settings({"active": "", "wa_template": ""}, self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:campaign_new") + "?lang=ar")
        self.assertContains(page, "data-kind-off")
        response = self.client.post(reverse("messaging:campaign_new"), self.form(action="queue"), follow=True)
        self.assertContains(response, "افتحها من إعدادات الحملات")
        self.assertEqual(Campaign.objects.get().status, CampaignStatus.DRAFT)
        self.assertFalse(OutboxMessage.objects.exists())

    def test_the_live_count_endpoint(self):
        self.client.force_login(self.cashier)  # reading numbers needs only the customer audience; it names no customer
        url = reverse("messaging:campaign_audience")
        data = self.client.get(url, {"audience": "inactive", "threshold": "30", "text": "أهلاً {name} عندك {points}", "name": "x", "lang": "ar"}).json()
        self.assertTrue(data["ok"])
        self.assertEqual((data["match"], data["no_phone"], data["duplicates"], data["count"]), (2, 1, 0, 1))
        self.assertEqual(data["preview"], "أهلاً سارة عندك 500")  # the first customer of the audience with a number
        self.assertIn("حط 1 رسالة", data["cta"])
        self.assertNotIn("منى", str(data))
        english = self.client.get(url, {"audience": "all", "text": "Hi {name}", "name": "x", "lang": "en"}).json()
        self.assertIn("daily cap (50 messages)", english["cap_note"])
        self.assertEqual(english["preview"], "Hi منى")
        bad = self.client.get(url, {"audience": "points", "threshold": "", "text": "x", "name": "x", "lang": "en"}).json()
        self.assertEqual(bad["ok"], False)
        self.assertIn("fewest points", bad["error"])

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=3)
    def test_the_composer_explains_the_cap(self):
        self.client.force_login(self.owner)
        data = self.client.get(reverse("messaging:campaign_audience"), {"audience": "all", "text": "x", "name": "x", "lang": "ar"}).json()
        self.assertTrue(data["cap_over"])
        self.assertIn("3 رسالة", data["cap_note"])
        self.assertIn("(1)", data["cap_note"])

    def test_the_list_and_the_outbox_link_in_both_languages(self):
        campaign = self.campaign(name="حملة الصيف")
        self.client.force_login(self.owner)
        for lang, word in (("ar", "حملات الولاء والرسائل"), ("en", "Loyalty and message campaigns")):
            page = self.client.get(reverse("messaging:campaigns") + f"?lang={lang}")
            self.assertContains(page, word)
            self.assertContains(page, "حملة الصيف")
            self.assertContains(page, "data-campaign-settings")
            self.assertContains(page, "MESSAGING_CAMPAIGN_DAILY_CAP")
            self.assertContains(self.client.get(reverse("messaging:outbox") + f"?lang={lang}"), 'data-campaigns-link')
        self.client.force_login(self.cashier)
        self.assertContains(self.client.get(reverse("messaging:outbox") + "?lang=ar"), f'href="{reverse("messaging:campaigns")}?lang=ar"')
        self.client.force_login(self.owner)
        self.client.post(reverse("messaging:campaigns"), {"lang": "ar", "action": "settings", "active": "1", "wa_template": "hesba_campaign"})
        self.assertEqual(services.template(MessageKind.CAMPAIGN).wa_template, "hesba_campaign")
        response = self.client.post(reverse("messaging:campaigns"), {"lang": "en", "action": "settings", "active": "1", "wa_template": "Bad Name"})
        self.assertContains(response, "lower-case letters")
        self.assertEqual(services.template(MessageKind.CAMPAIGN).wa_template, "hesba_campaign")
        self.assertTrue(AuditLog.objects.filter(action="save_campaign_settings").exists())
        campaign.refresh_from_db()

    def test_the_outbox_template_list_is_unchanged(self):
        self.assertEqual([row.kind for row in services.templates()], ["due", "instalment", "appointment", "refill", "workorder"])
        self.assertEqual(MessageTemplate.objects.filter(kind=MessageKind.CAMPAIGN).count(), 0)  # on by default without a row
        self.assertTrue(campaigns.active())

    def test_a_draft_can_be_deleted_but_a_queued_campaign_cannot(self):
        draft, queued = self.campaign(), self.campaign(name="اتحطت")
        campaigns.queue(queued, self.owner)
        campaigns.delete_draft(draft, self.owner)
        self.assertFalse(Campaign.objects.filter(pk=draft.pk).exists())
        with self.assertRaises(ValidationError):
            campaigns.delete_draft(queued, self.owner)
        with self.assertRaises(ValidationError):
            campaigns.save_draft(self.form(), self.owner, campaign=queued)


class CapLockAndEarlySendTests(CampaignSetup):
    """Codex #312: cap allocation is serialised per entity, and a message for a later day is never sent early."""

    def postgres(self, events):
        fake = mock.MagicMock()
        fake.vendor = "postgresql"
        fake.cursor.return_value.__enter__.return_value.execute.side_effect = lambda sql, params: events.append(("lock", sql, params))
        return fake

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=3)
    def test_queuing_takes_the_entity_lock_before_it_reads_the_room(self):
        from entities.services import main_entity

        events = []
        real_plan = campaigns.plan

        def plan(*args, **kwargs):
            events.append(("plan",))
            return real_plan(*args, **kwargs)

        with mock.patch.object(campaigns, "connection", self.postgres(events)), mock.patch.object(campaigns, "plan", side_effect=plan):
            campaigns.queue(self.campaign(), self.owner)
        self.assertEqual(events[0], ("lock", "SELECT pg_advisory_xact_lock(%s, %s)", [campaigns.CAP_LOCK_KEY, main_entity().pk]))
        self.assertEqual([event[0] for event in events], ["lock", "plan"])
        self.assertEqual(Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True)), {TODAY: 3, TODAY + timedelta(days=1): 1})

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=3)
    def test_carrying_forward_takes_the_same_lock_per_entity(self):
        from entities.services import main_entity

        campaigns.queue(self.campaign(), self.owner)
        events = []
        tomorrow = TODAY + timedelta(days=1)
        with mock.patch.object(campaigns, "connection", self.postgres(events)):
            services.build(tomorrow)
        self.assertEqual(events, [("lock", "SELECT pg_advisory_xact_lock(%s, %s)", [campaigns.CAP_LOCK_KEY, main_entity().pk])])
        per_day = Counter(OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).values_list("day", flat=True))
        self.assertTrue(all(n <= 3 for n in per_day.values()), per_day)

    def test_no_lock_is_issued_on_sqlite(self):
        events = []
        fake = self.postgres(events)
        fake.vendor = "sqlite"
        with mock.patch.object(campaigns, "connection", fake):
            campaigns.queue(self.campaign(), self.owner)
        self.assertEqual(events, [])

    def test_a_message_for_a_later_day_cannot_be_sent_early(self):
        later = TODAY + timedelta(days=2)
        campaigns.queue(self.campaign(scheduled_day=later), self.owner)
        message = OutboxMessage.objects.filter(kind=MessageKind.CAMPAIGN).first()
        for lang, fragment in (("ar", "معادها يوم"), ("en", "cannot be sent before then")):
            with self.assertRaises(ValidationError) as caught:
                services.mark(message, OutboxStatus.SENT, self.owner, lang)
            self.assertIn(fragment, " ".join(caught.exception.messages))
            self.assertIn(later.isoformat(), " ".join(caught.exception.messages))
        services.mark(message, OutboxStatus.SKIPPED, self.owner)  # skipping a deferred message stays possible

    @override_settings(MESSAGING_CAMPAIGN_DAILY_CAP=1)
    def test_a_stale_page_cannot_send_a_message_build_just_moved_to_tomorrow(self):
        from entities.services import main_entity

        def row(key, day, name):
            return OutboxMessage.objects.create(kind=MessageKind.CAMPAIGN, key=key, day=day, name=name, phone="201001234567", text="مرحبا", entity=main_entity())

        row("campaign:9:1", TODAY, "اليوم")  # today's one message already fills the cap
        stale = row("campaign:9:2", ago(1), "متأخرة")  # on the page the cashier opened yesterday
        self.client.force_login(self.owner)
        for lang, fragment in (("ar", "معادها يوم"), ("en", "cannot be sent before then")):
            response = self.client.post(reverse("messaging:outbox"), {"lang": lang, "action": "send", "message": stale.pk})
            self.assertEqual(response.status_code, 200, "refused on the page, not redirected to WhatsApp")
            self.assertContains(response, fragment)
            stale.refresh_from_db()
            self.assertEqual((stale.status, stale.day), (OutboxStatus.PENDING, TODAY + timedelta(days=1)))
            self.assertIsNone(stale.sent_at)

    def test_send_all_never_sends_a_deferred_message(self):
        campaigns.save_settings({"active": "1", "wa_template": "hesba_campaign"}, self.owner)
        campaigns.queue(self.campaign(scheduled_day=TODAY + timedelta(days=1)), self.owner)
        self.client.force_login(self.owner)
        with mock.patch.object(whatsapp, "missing", return_value=[]), mock.patch.object(whatsapp, "send", return_value=("wamid.1", "")) as send:
            self.client.post(reverse("messaging:outbox"), {"lang": "ar", "action": "send_all"})
        send.assert_not_called()
        self.assertFalse(OutboxMessage.objects.filter(status=OutboxStatus.SENT).exists())
