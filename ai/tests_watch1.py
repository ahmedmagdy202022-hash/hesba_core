"""Watch round 1, PH-04: the morning card's collections tile says it is yesterday's."""

from django.urls import reverse

from . import tests_voice_summary as base
from .summary import build_summary


class CollectionsTileTests(base.VoiceSummaryBase):
    def tile(self, lang):
        return {tile["key"]: tile for tile in build_summary(self.owner, lang).tiles}["receipts"]

    def test_the_tile_is_labelled_yesterdays_in_both_languages(self):
        ar, en = self.tile("ar"), self.tile("en")
        self.assertEqual((ar["label"], en["label"]), ("تحصيل امبارح", "Yesterday's collections"))
        # What it shows is yesterday's 75.25, not today's 10.00 (the label is only telling the truth).
        self.assertEqual(en["value"], "75.25 EGP")

    def test_the_link_stays_the_whole_list_because_the_list_takes_no_date(self):
        self.assertEqual(self.tile("en")["url"], f"{reverse('sales:payments')}?status=posted&lang=en")
        self.assertEqual(self.tile("ar")["sub"], "افتح قايمة التحصيلات كلها")
        self.client.force_login(self.owner)
        listing = self.client.get(reverse("sales:payments"), {"date_from": self.yesterday.isoformat(), "date_to": self.yesterday.isoformat()})
        self.assertEqual(listing.context["page"].paginator.count, 2)  # a date in the URL filters nothing: yesterday's and today's both show
