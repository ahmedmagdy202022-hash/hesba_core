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

#: The footnote under each page with a live provider (what the pages always said), byte for byte, and the sample's, which
#: must not claim what the label denies (F6/F7 of the video findings). Quotes are written the way a template escapes them.
LIVE_NOTES = {
    "voice_summary": ("الأرقام من تقارير حسبة نفسها. الصوت بس اللي متولّد.", "The figures come from Hesba&#x27;s own reports. Only the voice is generated."),
    "ask": ("الموديل بيختار التقرير بس. الأرقام نفسها من تقارير حسبة.", "The model only picks the report. The figures come from Hesba&#x27;s own reports."),
    "invoice_photo": ("الصورة بتتبعت لخدمة ذكاء اصطناعي برّه عشان تتقري. الأرقام بتتحسب من السطور في حسبة، والمسودة مش بتتحرك في المخزن ولا الخزنة لحد ما ترحّلها.",
                      "The photo is sent to an outside AI service to be read. The figures are computed from the lines in Hesba, and the draft moves no stock or cash until you post it."),
}
SAMPLE_NOTES = {
    "voice_summary": ("الأرقام من تقارير حسبة نفسها.", "The figures come from Hesba&#x27;s own reports."),
    "ask": ("الأرقام نفسها من تقارير حسبة.", "The figures come from Hesba&#x27;s own reports."),
    "invoice_photo": ("الأرقام بتتحسب من السطور في حسبة، والمسودة مش بتتحرك في المخزن ولا الخزنة لحد ما ترحّلها.",
                      "The figures are computed from the lines in Hesba, and the draft moves no stock or cash until you post it."),
}
#: What each live footnote claims and the sample label denies.
DENIED = {"voice_summary": ("الصوت بس اللي متولّد", "Only the voice is generated"), "ask": ("الموديل بيختار", "The model only picks"),
          "invoice_photo": ("بتتبعت لخدمة ذكاء اصطناعي", "sent to an outside AI service")}


class FootnoteMixin:
    """The footnote must follow the label: in mock it says nothing the label denies, with a live provider it is the original."""

    feature = ""

    def page(self, lang):  # pragma: no cover - set by the test
        raise NotImplementedError

    def test_the_sample_footnote_does_not_contradict_the_label(self):
        for index, lang in enumerate(("ar", "en")):
            with self.subTest(lang=lang):
                response = self.page(lang)
                self.assertContains(response, f'data-ai-sample="{self.feature}"')
                self.assertContains(response, f'>{SAMPLE_NOTES[self.feature][index]}</p>')
                self.assertNotContains(response, DENIED[self.feature][index])
                self.assertNotContains(response, LIVE_NOTES[self.feature][index])

    def test_the_live_footnote_is_unchanged(self):
        with override_settings(**ALL_LIVE):
            for index, lang in enumerate(("ar", "en")):
                with self.subTest(lang=lang):
                    response = self.page(lang)
                    self.assertNotContains(response, "data-ai-sample")
                    self.assertContains(response, f'>{LIVE_NOTES[self.feature][index]}</p>')
        self.assertEqual(self.calls(), 0)


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


# ---- one word for the sample (F8 of the video findings) ----

class SampleWordTests(AiSimpleTestCase):
    def test_the_three_labels_call_it_a_sample_in_the_same_word(self):
        for feature in ("voice_summary", "ask", "invoice_photo"):
            self.assertIn("عيّنة", WORDS["ar"][feature], feature)
            self.assertIn("sample", WORDS["en"][feature].lower(), feature)
        self.assertIn("عيّنة", WORDS["ar"]["sample_image"])  # the download link says it too, not «نموذج»
        for lang in ("ar", "en"):
            for text in WORDS[lang].values():
                self.assertNotIn("نموذج", text)


# ---- the label on each surface ----

class VoiceSummarySampleLabelTests(FootnoteMixin, VoiceSummaryBase):
    feature = "voice_summary"

    def page(self, lang):
        return self.dashboard(lang)

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


class AskSampleLabelTests(FootnoteMixin, AskBase):
    feature = "ask"

    def page(self, lang):
        self.client.force_login(self.owner)
        return self.client.get(f"{ASK_URL}?lang={lang}")

    def test_the_page_and_its_answer_say_they_are_a_sample_in_mock(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(ASK_URL), 'data-ai-sample="ask"')
        response = self.ask("مبيعات النهارده")
        self.assertEqual(response.context["answer"].kind, "figure")
        self.assertContains(response, 'data-ai-sample="ask"')
        self.assertContains(response, LABEL_AR)
        self.assertContains(response, "اختيار التقرير هنا عيّنة")
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


class InvoicePhotoSampleLabelTests(FootnoteMixin, PhotoBase):
    feature = "invoice_photo"

    def page(self, lang):
        self.client.force_login(self.owner)
        return self.client.get(f"{PHOTO_URL}?lang={lang}")

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
        self.assertContains(review, "فاتورة عيّنة ثابتة")
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
