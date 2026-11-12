"""Services video findings: the payment-link box says plainly that it needs the shop's own gateway account (f09)."""

from django.test import SimpleTestCase

from . import services


class GatewayNotConnectedSentenceTests(SimpleTestCase):
    def test_the_box_names_the_shops_own_gateway_account_in_both_languages(self):
        self.assertEqual(services.MESSAGES["ar"]["not_connected"],
                         "بوابة الدفع غير موصّلة على السيرفر، ولينك الدفع أونلاين محتاج حساب بوابة دفع خاص بالمحل (Paymob)، فمينفعش يتعمل لينك دلوقتي.")
        self.assertEqual(services.MESSAGES["en"]["not_connected"],
                         "The payment gateway is not connected on the server: online payment links need the shop's own payment-gateway account (Paymob), so no link can be made yet.")
