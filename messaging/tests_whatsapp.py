"""T9 (live part): messages with an approved template go by the shop's WhatsApp Business account (Cloud API)."""

import os
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command

from . import services, whatsapp
from .models import MessageKind, MessageTemplate, OutboxMessage, OutboxStatus
from .tests import OutboxSetup

ENV = {"WHATSAPP_TOKEN": "wa-token", "WHATSAPP_PHONE_NUMBER_ID": "1234567890", "WHATSAPP_API_VERSION": "v21.0", "WHATSAPP_LANGUAGE": "ar"}


class FakeGraph:
    def __init__(self, refuse=False):
        self.refuse, self.calls = refuse, []

    def __call__(self, url, body, token):
        self.calls.append({"url": url, "body": body, "token": token})
        if self.refuse:
            return 400, {"error": {"code": 132001, "message": "Template name does not exist in the translation"}}
        return 200, {"messaging_product": "whatsapp", "messages": [{"id": "wamid.ABC"}]}


class SendingTests(OutboxSetup):
    def setUp(self):
        super().setUp()
        services.build()
        self.message = OutboxMessage.objects.get()

    def name_template(self, name="hesba_reminder"):
        services.save_template(MessageKind.DUE, {"text": services.template(MessageKind.DUE).text, "active": "1", "every_days": "7", "wa_template": name}, self.owner)

    def test_not_connected_means_one_click_as_before(self):
        self.assertFalse(services.api_connected())
        self.name_template()
        fake = FakeGraph()
        with mock.patch.object(whatsapp, "_http", fake):
            self.assertEqual(services.send_by_api(), {"sent": 0, "failed": 0})
        self.assertEqual(fake.calls, [])

    def test_a_kind_with_an_approved_template_goes_by_itself(self):
        self.name_template()
        fake = FakeGraph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", fake):
            self.assertTrue(services.api_connected())
            self.assertEqual(services.send_by_api(self.owner), {"sent": 1, "failed": 0})
        call = fake.calls[0]
        self.assertEqual((call["url"], call["token"]), ("https://graph.facebook.com/v21.0/1234567890/messages", "wa-token"))
        template = call["body"]["template"]
        self.assertEqual((call["body"]["to"], template["name"], template["language"]["code"]), ("201001234567", "hesba_reminder", "ar"))
        company, text = template["components"][0]["parameters"]
        self.assertEqual(text["text"], whatsapp.parameter(self.message.text))
        self.message.refresh_from_db()
        self.assertEqual((self.message.status, self.message.channel, self.message.provider_id), (OutboxStatus.SENT, "api", "wamid.ABC"))

    def test_a_kind_without_a_template_waits_for_a_click(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", FakeGraph()):
            self.assertEqual(services.send_by_api(), {"sent": 0, "failed": 0})
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, OutboxStatus.PENDING)

    def test_a_refused_message_keeps_waiting_with_the_reason(self):
        self.name_template()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", FakeGraph(refuse=True)):
            self.assertEqual(services.send_by_api(), {"sent": 0, "failed": 1})
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, OutboxStatus.PENDING)
        self.assertIn("132001", self.message.error)

    def test_a_parameter_never_carries_new_lines_or_long_runs_of_spaces(self):
        self.assertEqual(whatsapp.parameter("سطر\nتاني\t     هنا"), "سطر تاني    هنا".replace("    ", "   "))

    def test_template_names_are_checked(self):
        with self.assertRaises(ValidationError):
            self.name_template("Hesba Reminder")
        self.assertEqual(MessageTemplate.objects.filter(wa_template__gt="").count(), 0)

    def test_the_outbox_button_and_the_command(self):
        self.name_template()
        self.client.force_login(self.owner)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", FakeGraph()):
            self.assertContains(self.client.get("/messages/"), "data-send-all")
            self.client.post("/messages/", {"action": "send_all", "lang": "ar"})
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, OutboxStatus.SENT)
        OutboxMessage.objects.filter(pk=self.message.pk).update(status=OutboxStatus.PENDING)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", FakeGraph()):
            call_command("send_messages", verbosity=0)
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, OutboxStatus.SENT)
