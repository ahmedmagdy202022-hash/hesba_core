"""DEMO-001: the showcase install fills itself once, and only in demo mode."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from restaurant.models import DiningTable
from sales.models import SalesInvoice
from settings_core.models import ClientProfile


class DemoTests(TestCase):
    def test_refuses_outside_demo_mode(self):
        with self.assertRaises(CommandError):
            call_command("prepare_demo", stdout=StringIO())
        self.assertFalse(ClientProfile.objects.exists())

    @override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1")
    def test_fills_an_empty_database_once_and_the_logins_work(self):
        call_command("prepare_demo", stdout=StringIO())
        self.assertTrue(SalesInvoice.objects.filter(status="posted").exists())
        self.assertEqual(DiningTable.objects.count(), 6)
        # DEMO-FEEDBACK: a month of history, at shop hours rather than all "now".
        past = SalesInvoice.objects.filter(invoice_date__lt=timezone.localdate())
        self.assertGreaterEqual(past.count(), 20)
        self.assertGreater(len({timezone.localtime(i.created_at).hour for i in past}), 5)
        invoices = SalesInvoice.objects.count()
        out = StringIO()
        call_command("prepare_demo", stdout=out)
        self.assertIn("already present", out.getvalue())
        self.assertEqual(DiningTable.objects.count(), 6)
        self.assertEqual(SalesInvoice.objects.count(), invoices)  # today was already topped up
        self.assertTrue(self.client.login(username="cashier", password="Demo-pass-1"))
        self.assertContains(self.client.get(reverse("login")), "data-demo-login")

    def test_no_demo_banner_on_a_real_install(self):
        self.assertNotContains(self.client.get(reverse("login")), "data-demo-login")
