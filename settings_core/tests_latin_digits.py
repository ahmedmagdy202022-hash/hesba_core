"""FB-23: Arabic digits typed into a number box become 0-9 instead of being dropped without a word."""

from decimal import Decimal

from django import forms
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase


class LatinDigitsTests(SimpleTestCase):
    def test_every_shell_loads_the_conversion(self):
        for name in ("partials/app_shell.html", "master_data/base.html", "operations/base.html", "reports/dashboard.html"):
            with self.subTest(template=name):
                self.assertIn("hesba/js/shell.js", get_template(name).template.source)
        with open(finders.find("hesba/js/shell.js"), encoding="utf-8") as handle:
            js = handle.read()
        for needed in ("FB-23", "\\u0660-\\u0669\\u06F0-\\u06F9", "beforeinput", "paste", "'number'", "'decimal'", "event.preventDefault()", "!el.readOnly && !el.disabled"):
            with self.subTest(needed=needed):
                self.assertIn(needed, js)

    def test_the_server_reads_arabic_digits_too(self):
        class Amount(forms.Form):
            amount = forms.DecimalField(decimal_places=2)
            count = forms.IntegerField()

        form = Amount({"amount": "١٢.٥٠", "count": "٧"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["amount"], form.cleaned_data["count"]), (Decimal("12.50"), 7))

    def test_a_phone_in_arabic_digits_makes_a_working_whatsapp_link(self):
        from reports.aging import whatsapp_number

        self.assertEqual(whatsapp_number("٠١٠٠١٢٣٤٥٦٧"), "201001234567")
        self.assertEqual(whatsapp_number("01001234567"), "201001234567")
        self.assertEqual(whatsapp_number("+٩٦٦ ٥٠ ١٢٣ ٤٥٦٧"), "966501234567")
