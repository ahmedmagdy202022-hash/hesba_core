"""Watch round 2 fixes for the salon: BE-01 (a deposit may not exceed the booking's price)."""

from decimal import Decimal as D

from django.urls import reverse

from deposits.models import Deposit
from deposits.tests import DepositSetup, cash_balance, customer_balance
from sales.models import CustomerPayment


TOO_BIG_AR = "العربون مينفعش يزيد عن سعر الحجز. أقصى عربون ممكن 300.00."
TOO_BIG_EN = "The deposit cannot be more than the booking price. The most you can take is 300.00."


class DepositCeilingTests(DepositSetup):
    """The service is «صيانة تكييف» at 300.00; the booking inherits that price."""

    def post_deposit(self, appointment, amount, lang="ar"):
        self.client.force_login(self.owner)
        url = f"{reverse('appointments:detail', args=[appointment.pk])}?lang={lang}"
        return self.client.post(url, {"lang": lang, "action": "deposit", "amount": amount, "cashbox": str(self.cashbox.pk)})

    def assert_nothing_taken(self, appointment):
        self.assertFalse(Deposit.objects.exists())
        self.assertFalse(CustomerPayment.objects.exists())
        self.assertEqual((customer_balance(self.customer), cash_balance(self.cashbox)), (D("0.00"), D("0.00")))

    def test_a_deposit_above_the_price_is_refused_in_arabic_and_takes_no_money(self):
        appointment = self.book()
        self.assertEqual(appointment.price, D("300.00"))
        answer = self.post_deposit(appointment, "999999")
        self.assertEqual(answer.status_code, 200)
        self.assertContains(answer, TOO_BIG_AR)
        self.assert_nothing_taken(appointment)

    def test_a_deposit_above_the_price_is_refused_in_english(self):
        appointment = self.book()
        self.assertContains(self.post_deposit(appointment, "300.01", lang="en"), TOO_BIG_EN)
        self.assert_nothing_taken(appointment)

    def test_a_deposit_equal_to_the_price_is_taken(self):
        appointment = self.book()
        self.assertEqual(self.post_deposit(appointment, "300.00").status_code, 302)
        self.assertEqual(Deposit.objects.get().amount, D("300.00"))
        self.assertEqual((customer_balance(self.customer), cash_balance(self.cashbox)), (D("-300.00"), D("300.00")))

    def test_the_limit_follows_the_price_typed_on_the_booking_not_the_services_list_price(self):
        appointment = self.book(price=D("120.00"))
        self.assertContains(self.post_deposit(appointment, "150"), "أقصى عربون ممكن 120.00.")
        self.assert_nothing_taken(appointment)
        self.assertEqual(self.post_deposit(appointment, "120").status_code, 302)

    def test_a_booking_with_no_price_keeps_the_old_behaviour(self):
        appointment = self.book(price=D("0.00"))
        self.assertEqual(appointment.price, D("0.00"))
        self.assertEqual(self.post_deposit(appointment, "999").status_code, 302)
        self.assertEqual(Deposit.objects.get().amount, D("999.00"))

    def test_zero_and_letters_still_get_the_services_own_message(self):
        appointment = self.book()
        self.assertContains(self.post_deposit(appointment, "0"), "مبلغ العربون مش صحيح")
        self.assertContains(self.post_deposit(appointment, "abc", lang="en"), "Invalid deposit amount.")
        self.assert_nothing_taken(appointment)
