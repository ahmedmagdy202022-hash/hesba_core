"""AI demo (HG-078 deviation 7): the mock may run on a showcase install only, and every mock answer is labelled.

* Resolution and checks: an empty ``AI_MODE`` is ``mock`` with ``DEMO_MODE`` on even when ``DEBUG`` is off; an explicit
  ``mock`` starts there (no ``ai.E001``) but still refuses to start with ``DEBUG`` and ``DEMO_MODE`` both off; ``live`` on a
  sandboxed showcase is still ``ai.E006``.
* The label: present in mock on the voice summary card (A1), the «اسأل حسبة» page (A2) and the invoice photo pages (A3);
  absent in live and off.

Every test runs on the AI no-network base. The surfaces reuse each feature's own fixtures.
"""

from django.test import override_settings

from . import providers
from .checks import ai_settings
from .no_network import AiSimpleTestCase
from .providers.mock import MockClaudeInvoiceReader, MockClaudeRouter, MockElevenLabsTTS
from .sample import WORDS
from .tests import LIVE_KEYS, ids
from .tests_ask import URL as ASK_URL, AskBase
from .tests_invoice_photo import URL as PHOTO_URL, PhotoBase
from .tests_voice_summary import VoiceSummaryBase

#: Live with every key it checks for (ElevenLabs and Anthropic), so no other check fires.
ALL_LIVE = dict(LIVE_KEYS, ANTHROPIC_API_KEY="test-anthropic-key-not-real", ANTHROPIC_MODEL="claude-opus-5-5")
LABEL_AR, LABEL_EN = WORDS["ar"]["title"], WORDS["en"]["title"]


class DemoModeResolutionTests(AiSimpleTestCase):
    def test_an_empty_mode_is_the_mock_on_a_showcase_even_with_debug_off(self):
        with override_settings(DEBUG=False, DEMO_MODE=True, AI_MODE=""):
            self.assertEqual(providers.mode(), providers.MOCK)
            self.assertTrue(providers.is_mock())
            self.assertIsInstance(providers.get(providers.ELEVENLABS_TTS), MockElevenLabsTTS)
            self.assertIsInstance(providers.get(providers.CLAUDE_ROUTER), MockClaudeRouter)
            self.assertIsInstance(providers.get(providers.CLAUDE_INVOICE_READER), MockClaudeInvoiceReader)
            self.assertEqual(ids(ai_settings(None)), [])

    def test_an_empty_mode_is_still_off_on_a_real_server(self):
        with override_settings(DEBUG=False, DEMO_MODE=False, AI_MODE=""):
            self.assertEqual(providers.mode(), providers.OFF)
            self.assertFalse(providers.is_mock())
            self.assertIsNone(providers.get(providers.ELEVENLABS_TTS))
            self.assertIsNone(providers.get(providers.CLAUDE_ROUTER))
            self.assertIsNone(providers.get(providers.CLAUDE_INVOICE_READER))

    def test_an_explicit_mock_starts_on_a_showcase_and_is_refused_everywhere_else_with_debug_off(self):
        with override_settings(DEBUG=False, DEMO_MODE=True, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), [])
        with override_settings(DEBUG=False, DEMO_MODE=True, DEMO_SANDBOXES=True, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), [])  # a sandboxed showcase: the mock spends nothing
        with override_settings(DEBUG=False, DEMO_MODE=False, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E001"])
        with override_settings(DEBUG=False, DEMO_MODE=False, DEMO_SANDBOXES=True, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E001"])  # sandboxes alone are not a showcase
        with override_settings(DEBUG=True, DEMO_MODE=False, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), [])

    def test_live_stays_refused_on_a_sandboxed_showcase(self):
        with override_settings(DEBUG=False, DEMO_MODE=True, DEMO_SANDBOXES=True, **ALL_LIVE):
            self.assertEqual(ids(ai_settings(None)), ["ai.E006"])
            self.assertFalse(providers.is_mock())
        with override_settings(DEBUG=False, DEMO_MODE=True, DEMO_SANDBOXES=False, **ALL_LIVE):
            self.assertEqual(ids(ai_settings(None)), [])  # a non-sandboxed demo with a key stays Ahmed's call
        with override_settings(DEBUG=False, DEMO_MODE=True, AI_MODE="off"):
            self.assertEqual(providers.mode(), providers.OFF)
            self.assertEqual(ids(ai_settings(None)), [])

    def test_an_unknown_mode_is_still_refused_on_a_showcase(self):
        with override_settings(DEBUG=False, DEMO_MODE=True, AI_MODE="maybe"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E003"])
            self.assertEqual(providers.mode(), providers.OFF)


# ---- the label on each surface ----

class VoiceSummarySampleLabelTests(VoiceSummaryBase):
    def dashboard(self, lang="ar"):
        self.login()
        response = self.client.get(f"/dashboard/?lang={lang}")
        self.assertContains(response, "data-voice-summary")
        return response

    def test_the_card_says_it_is_a_sample_in_mock(self):
        response = self.dashboard()
        self.assertContains(response, 'data-ai-sample="voice_summary"')
        self.assertContains(response, LABEL_AR)
        self.assertContains(response, "الصوت عيّنة ساكتة")
        self.assertContains(self.dashboard("en"), LABEL_EN)

    def test_no_label_in_live_or_off(self):
        for settings in (ALL_LIVE, {"AI_MODE": "off"}):
            with self.subTest(settings=settings["AI_MODE"]), override_settings(**settings):
                response = self.dashboard()
                self.assertNotContains(response, "data-ai-sample")
                self.assertNotContains(response, LABEL_AR)
        self.assertEqual(self.calls(), 0)


class AskSampleLabelTests(AskBase):
    def test_the_page_and_its_answer_say_they_are_a_sample_in_mock(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(ASK_URL), 'data-ai-sample="ask"')
        response = self.ask("مبيعات النهارده")
        self.assertEqual(response.context["answer"].kind, "figure")
        self.assertContains(response, 'data-ai-sample="ask"')
        self.assertContains(response, LABEL_AR)
        self.assertContains(self.ask("Sales today", lang="en"), LABEL_EN)

    def test_no_label_in_live_or_off(self):
        self.client.force_login(self.owner)
        for settings in (ALL_LIVE, {"AI_MODE": "off"}):
            with self.subTest(settings=settings["AI_MODE"]), override_settings(**settings):
                response = self.client.get(ASK_URL)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "data-ai-sample")
                self.assertNotContains(response, LABEL_AR)
        self.assertEqual(self.calls(), 0)

    def test_no_label_for_someone_who_may_ask_nothing(self):
        from .tests_voice_summary import nobody

        self.client.force_login(nobody("ask_label_nobody"))
        response = self.client.get(ASK_URL)
        self.assertNotContains(response, "data-ai-sample", status_code=403)


class InvoicePhotoSampleLabelTests(PhotoBase):
    def test_the_upload_and_review_pages_say_they_are_a_sample_in_mock(self):
        self.client.force_login(self.owner)
        upload = self.client.get(PHOTO_URL)
        self.assertContains(upload, 'data-ai-sample="invoice_photo"')
        self.assertContains(upload, LABEL_AR)
        self.assertContains(upload, "/static/hesba/img/demo/sample-invoice.jpg")  # the sample photo to try
        token = self.token_of(self.upload())
        review = self.client.get(f"{PHOTO_URL}{token}/")
        self.assertContains(review, "data-iph-review")
        self.assertContains(review, 'data-ai-sample="invoice_photo"')
        self.assertContains(review, "فاتورة نموذج ثابتة")
        self.assertNotContains(review, "data-ai-sample-image")  # the download link is on the upload page only
        self.assertContains(self.client.get(f"{PHOTO_URL}{token}/?lang=en"), LABEL_EN)

    def test_no_label_in_live_or_off(self):
        token = self.token_of(self.upload())  # read in mock; the pages below only show it
        for settings in (ALL_LIVE, {"AI_MODE": "off"}):
            with self.subTest(settings=settings["AI_MODE"]), override_settings(**settings):
                for url in (PHOTO_URL, f"{PHOTO_URL}{token}/"):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, "data-ai-sample")
                    self.assertNotContains(response, LABEL_AR)

    def test_no_label_on_the_refusal_page(self):
        self.client.force_login(self.cashier)
        response = self.client.get(PHOTO_URL)
        self.assertNotContains(response, "data-ai-sample", status_code=403)
