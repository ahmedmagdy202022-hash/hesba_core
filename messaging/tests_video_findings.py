"""Services video findings: the campaign composer says how many customers each audience can reach before anything is sent (f08)
and the outbox's recipient column has a word, not a lone prefix (f10)."""

import re

from django.urls import reverse

from loyalty.models import PointsEntry
from settings_core import capabilities as caps
from settings_core.models import ClientProfile

from .models import Campaign, CampaignAudience
from .tests_campaigns import CampaignSetup


def option(html, value):
    """The <label> of one audience option."""

    return re.search(rf'<label class="cp-seg[^"]*" data-segment="{value}">.*?</label>', html, re.S).group(0)


class AudienceOptionsTests(CampaignSetup):
    def page(self, **params):
        self.client.force_login(self.owner)
        return self.client.get(reverse("messaging:campaign_new"), params).content.decode()

    def test_each_option_says_how_many_customers_it_can_reach(self):
        html = self.page()
        # the fixture: 4 customers with a valid, distinct mobile; 3 of them have points; 3 of them have bought before
        self.assertIn('<small class="cp-seg__note" data-segment-note>4 عميل ليهم موبايل</small>', option(html, "all"))
        self.assertIn(">3 عميل عندهم نقاط<", option(html, "points"))
        self.assertIn(">3 عميل اشتروا قبل كده<", option(html, "inactive"))
        self.assertIn("الرقم بيظهر بعد ما تختار الفئة", option(html, "category"))
        self.assertIn("الرقم بيظهر بعد ما تختار الفئة", option(html, "lapsed"))
        self.assertNotIn("disabled", html.split('data-segment="all"')[1].split("</label>")[0])

    def test_english_has_its_own_sentences(self):
        html = self.page(lang="en")
        self.assertIn(">4 customers with a mobile<", option(html, "all"))
        self.assertIn(">3 customers have points<", option(html, "points"))

    def test_with_loyalty_off_the_points_option_is_closed_and_says_why(self):
        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", False)
        points = option(self.page(), "points")
        self.assertIn(" disabled", points)
        self.assertIn("الولاء مقفول في الإعدادات.", points)
        self.assertNotIn(" disabled", option(self.page(), "all"))

    def test_an_audience_nobody_fits_is_explained_and_closed(self):
        PointsEntry.objects.all().delete()
        points = option(self.page(), "points")
        self.assertIn(" disabled", points)
        self.assertIn("مفيش عميل يناسب الشريحة دي دلوقتي.", points)

    def test_a_draft_already_on_a_closed_audience_still_opens_with_it_chosen(self):
        PointsEntry.objects.all().delete()
        draft = self.campaign(audience=CampaignAudience.POINTS, threshold=100)
        self.client.force_login(self.owner)
        points = option(self.client.get(reverse("messaging:campaign", args=[draft.pk])).content.decode(), "points")
        self.assertIn(" checked", points)
        self.assertNotIn(" disabled", points)

    def test_the_server_still_refuses_a_closed_audience_when_it_is_posted_by_hand(self):
        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", False)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("messaging:campaign_new"), self.form(audience="points", threshold="100"))
        self.assertContains(response, "الولاء مقفول في الإعدادات.")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Campaign.objects.count(), 0)  # nothing was saved

    def test_no_threshold_is_still_refused_after_submit(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("messaging:campaign_new"), self.form(audience="points", threshold=""))
        self.assertContains(response, "اكتب أقل عدد نقاط (1 أو أكتر).")
        self.assertEqual(Campaign.objects.count(), 0)
