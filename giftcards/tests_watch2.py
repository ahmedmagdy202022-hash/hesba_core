"""Watch round 2 fixes for gift cards: BE-02 (an absurd card value is refused)."""

from decimal import Decimal as D

from django.urls import reverse

from .models import GiftCard
from .tests import GiftSetup, box_balance


TOO_BIG_AR = "قيمة الكارت كبيرة أوي. أقصى قيمة للكارت الواحد 10,000,000.00."
TOO_BIG_EN = "The card value is too large. The most one card can hold is 10,000,000.00."


class FaceValueLimitTests(GiftSetup):
    def sell_from_screen(self, value, lang="ar"):
        self.client.force_login(self.cashier)
        return self.client.post(reverse("giftcards:list"), {"lang": lang, "face_value": value, "cashbox": str(self.cash.pk)}, follow=True)

    def test_an_absurd_value_is_refused_in_arabic_and_no_money_moves(self):
        answer = self.sell_from_screen("99999999999")
        self.assertContains(answer, TOO_BIG_AR)
        self.assertFalse(GiftCard.objects.exists())
        self.assertEqual(box_balance(self.cash), D("0.00"))

    def test_an_absurd_value_is_refused_in_english(self):
        self.assertContains(self.sell_from_screen("10000000.01", lang="en"), TOO_BIG_EN)
        self.assertFalse(GiftCard.objects.exists())

    def test_the_limit_itself_still_sells(self):
        self.sell_from_screen("10000000")
        self.assertEqual(GiftCard.objects.get().face_value, D("10000000.00"))
        self.assertEqual(box_balance(self.cash), D("10000000.00"))

    def test_zero_negative_and_letters_are_still_refused(self):
        for value in ("0", "-5", "abc"):
            self.assertContains(self.sell_from_screen(value, lang="en"), "Invalid card amount")
        self.assertFalse(GiftCard.objects.exists())
