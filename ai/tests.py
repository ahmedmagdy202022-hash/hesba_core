"""HG-078 (A0): the AI provider layer. Every test here runs under the no-network guard (ai/no_network.py)."""

import io
import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from unittest import mock

from django.core.checks import run_checks
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities.models import Entity
from hesba_testing.factories import make_user
from settings_core import capabilities as caps
from settings_core.models import FeatureFlag
from settings_core.ui_messages import translate

from . import providers, services
from .checks import ai_settings
from .no_network import AiSimpleTestCase, AiTestCase, RealNetworkAttempt
from .providers.base import ProviderError
from .providers.elevenlabs import ElevenLabsTTS
from .providers.mock import SILENT_MP3, MockElevenLabsTTS

CAIRO = services.CAIRO
LIVE_KEYS = {"AI_MODE": "live", "ELEVENLABS_API_KEY": "test-key-not-real", "ELEVENLABS_VOICE_ID": "test-voice"}
# Stands for what A1's text could carry; none of it may reach the audit row.
SENTINEL = "Mona Hassan 01001234567 paid 98765.43"


def ids(errors):
    return sorted(error.id for error in errors)


#: The live client's one way out (an opener that refuses redirects); tests replace it, never the network.
OPEN = "ai.providers.elevenlabs._open"


class FakeResponse:
    def __init__(self, body=SILENT_MP3, content_type="audio/mpeg"):
        self.body, self.headers = body, {"Content-Type": content_type}
        self.stream = io.BytesIO(body)

    def read(self, limit=-1):
        return self.stream.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CheckTests(AiSimpleTestCase):
    def test_mock_is_refused_only_when_explicit_and_debug_is_off(self):
        with override_settings(DEBUG=False, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E001"])
        with override_settings(DEBUG=True, AI_MODE="mock"):
            self.assertEqual(ids(ai_settings(None)), [])
        with override_settings(DEBUG=False, AI_MODE=""):  # a server that never set AI: nothing connected, starts fine
            self.assertEqual(ids(ai_settings(None)), [])
            self.assertEqual(providers.mode(), providers.OFF)
        with override_settings(DEBUG=True, AI_MODE=""):
            self.assertEqual(providers.mode(), providers.MOCK)

    def test_live_needs_the_key_and_a_real_voice(self):
        with override_settings(DEBUG=False, AI_MODE="live", ELEVENLABS_API_KEY="", ELEVENLABS_VOICE_ID="test-voice"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E002"])
        with override_settings(DEBUG=False, AI_MODE="live", ELEVENLABS_API_KEY="  ", ELEVENLABS_VOICE_ID="test-voice"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E002"])
        with override_settings(DEBUG=False, AI_MODE="live", ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID=providers.PLACEHOLDER_VOICE):
            self.assertEqual(ids(ai_settings(None)), ["ai.E004"])
        with override_settings(DEBUG=False, **LIVE_KEYS):
            self.assertEqual(ids(ai_settings(None)), [])

    def test_live_needs_a_model(self):
        with override_settings(DEBUG=False, ELEVENLABS_MODEL_ID="  ", **LIVE_KEYS):
            self.assertEqual(ids(ai_settings(None)), ["ai.E005"])
            self.assertIsNone(providers.get(providers.ELEVENLABS_TTS))
        with override_settings(DEBUG=False, AI_MODE="mock", ELEVENLABS_MODEL_ID=""):
            self.assertEqual(ids(ai_settings(None)), ["ai.E001"])  # the model is checked for live only

    def test_unknown_mode_is_refused_and_connects_nothing(self):
        with override_settings(AI_MODE="maybe"):
            self.assertEqual(ids(ai_settings(None)), ["ai.E003"])
            self.assertEqual(providers.mode(), providers.OFF)
            self.assertIsNone(providers.get(providers.ELEVENLABS_TTS))

    def test_the_check_is_registered(self):
        with override_settings(DEBUG=False, AI_MODE="mock"):
            self.assertIn("ai.E001", ids(run_checks()))

    def test_provider_choice_follows_the_mode(self):
        self.assertIsInstance(providers.get(providers.ELEVENLABS_TTS), MockElevenLabsTTS)
        with override_settings(AI_MODE="off"):
            self.assertIsNone(providers.get(providers.ELEVENLABS_TTS))
        with override_settings(AI_MODE="live", ELEVENLABS_API_KEY=""):  # half-configured live: none, never the mock
            self.assertIsNone(providers.get(providers.ELEVENLABS_TTS))
        with override_settings(**LIVE_KEYS):
            live = providers.get(providers.ELEVENLABS_TTS)
            self.assertIs(type(live), ElevenLabsTTS)
            self.assertTrue(live.live)


class MockProviderTests(AiSimpleTestCase):
    def test_mock_returns_the_same_small_silent_mp3(self):
        provider = providers.get(providers.ELEVENLABS_TTS)
        first, second = provider.synthesize("مرحبا"), provider.synthesize("something else")
        self.assertEqual(first.content_type, "audio/mpeg")
        self.assertEqual(first.size, 1440)
        self.assertEqual(first.audio, second.audio)
        self.assertTrue(first.audio.startswith(b"\xff\xfb\x18\xc0"))
        self.assertTrue(all(first.audio[offset:offset + 4] == b"\xff\xfb\x18\xc0" for offset in range(0, 1440, 144)))
        self.assertEqual(provider.calls[0]["body"], {"text": "مرحبا", "model_id": "eleven_multilingual_v2"})
        self.assertTrue(provider.calls[0]["url"].startswith("https://api.elevenlabs.invalid/v1/text-to-speech/"))


class LiveClientRequestTests(AiSimpleTestCase):
    def tts(self, voice="test-voice"):
        return ElevenLabsTTS(api_key="test-key-not-real", voice_id=voice, model_id="eleven_multilingual_v2")

    def test_the_request_matches_the_public_api(self):
        with mock.patch(OPEN, return_value=FakeResponse()) as urlopen:
            speech = self.tts("a b/c").synthesize("ملخص اليوم", voice_settings={"stability": 0.5, "similarity_boost": 0.75})
        request = urlopen.call_args.args[0]
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 10})
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://api.elevenlabs.io/v1/text-to-speech/a%20b%2Fc")
        self.assertEqual(request.get_header("Xi-api-key"), "test-key-not-real")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept"), "audio/mpeg")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "text": "ملخص اليوم", "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}})
        self.assertEqual((speech.content_type, speech.audio), ("audio/mpeg", SILENT_MP3))

    def test_voice_settings_are_left_out_when_not_given(self):
        with mock.patch(OPEN, return_value=FakeResponse()) as urlopen:
            self.tts().synthesize("hello")
        self.assertEqual(json.loads(urlopen.call_args.args[0].data), {"text": "hello", "model_id": "eleven_multilingual_v2"})

    def test_http_errors_become_provider_errors_without_the_key_or_text(self):
        error = urllib.error.HTTPError("https://api.elevenlabs.io/v1/text-to-speech/test-voice", 401, "Unauthorized", {},
                                       io.BytesIO(b'{"detail": "invalid key test-key-not-real"}'))
        with mock.patch(OPEN, side_effect=error), self.assertRaises(ProviderError) as caught:
            self.tts().synthesize(SENTINEL)
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(str(caught.exception), "ElevenLabs answered 401.")
        for outcome in (urllib.error.URLError("down"), TimeoutError(), ConnectionResetError()):
            with self.subTest(outcome=type(outcome).__name__), mock.patch(OPEN, side_effect=outcome), \
                    self.assertRaisesMessage(ProviderError, "ElevenLabs could not be reached."):
                self.tts().synthesize("hello")

    def test_an_answer_cut_off_midway_is_a_provider_error(self):
        import http.client

        class CutOff(FakeResponse):
            def read(self, limit=-1):
                raise http.client.IncompleteRead(b"partial", 1000)

        with mock.patch(OPEN, return_value=CutOff()), \
                self.assertRaisesMessage(ProviderError, "ElevenLabs could not be reached."):
            self.tts().synthesize("hello")

    def test_an_answer_that_is_not_audio_is_refused(self):
        for response in (FakeResponse(b'{"ok": true}', "application/json"), FakeResponse(b"", "audio/mpeg")):
            with self.subTest(content_type=response.headers["Content-Type"]), mock.patch(OPEN, return_value=response), \
                    self.assertRaisesMessage(ProviderError, "ElevenLabs gave no audio."):
                self.tts().synthesize("hello")

    def test_a_trickling_answer_hits_the_total_deadline(self):
        clock = {"now": 1000.0}

        class Trickle(FakeResponse):
            def read(self, limit=-1):
                clock["now"] += 3  # each chunk arrives inside the per-read timeout, but they add up
                return b"\x00"

        with mock.patch(OPEN, return_value=Trickle()), mock.patch("ai.providers.elevenlabs.time.monotonic", side_effect=lambda: clock["now"]), \
                self.assertRaisesMessage(ProviderError, "ElevenLabs took too long to answer."):
            self.tts().synthesize("hello")
        self.assertLessEqual(clock["now"] - 1000.0, 21 + 3)  # stopped at the 20 s deadline, not after the whole body

    def test_the_body_is_read_in_chunks_and_capped(self):
        with mock.patch(OPEN, return_value=FakeResponse(SILENT_MP3 * 100)):  # several 64 KB chunks
            self.assertEqual(self.tts().synthesize("hello").size, len(SILENT_MP3) * 100)
        with mock.patch("ai.providers.elevenlabs.MAX_AUDIO_BYTES", 1000), mock.patch(OPEN, return_value=FakeResponse()), \
                self.assertRaisesMessage(ProviderError, "ElevenLabs answered with more audio than allowed."):
            self.tts().synthesize("hello")

    def test_a_redirect_is_refused_and_the_key_is_not_sent_on(self):
        import email.message
        import urllib.response

        from .providers import elevenlabs

        seen = []

        class Redirecting(urllib.request.HTTPSHandler):
            """Stands in for the network: answers every request with a 302 to another host."""

            def https_open(self, req):
                seen.append((req.full_url, req.get_header("Xi-api-key")))
                headers = email.message.Message()
                headers["Location"] = "https://elsewhere.invalid/steal"
                response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, code=302)
                response.msg = "Found"
                return response

        with mock.patch.object(elevenlabs, "_OPENER", elevenlabs._build_opener(Redirecting)), \
                self.assertRaises(ProviderError) as caught:
            self.tts().synthesize("hello")
        self.assertEqual((str(caught.exception), caught.exception.status), ("ElevenLabs answered 302.", 302))
        self.assertEqual(seen, [("https://api.elevenlabs.io/v1/text-to-speech/test-voice", "test-key-not-real")])  # never followed


class NoNetworkGuardTests(AiSimpleTestCase):
    def test_the_guard_refuses_and_records_every_way_out(self):
        attempts = (lambda: urllib.request.urlopen("https://api.elevenlabs.io/"),
                    lambda: socket.create_connection(("api.elevenlabs.io", 443)),
                    lambda: socket.socket().connect(("127.0.0.1", 9)))
        for attempt in attempts:
            with self.assertRaises(RealNetworkAttempt):
                attempt()
        self.assertEqual(self.network_attempts, ["urllib.request.urlopen", "socket.create_connection", "socket.socket.connect"])
        self.network_attempts.clear()  # handled here; any other test that tries fails at cleanup

    def test_the_live_client_cannot_swallow_the_guard(self):
        with self.assertRaises(RealNetworkAttempt):  # not turned into a ProviderError
            ElevenLabsTTS(api_key="k", voice_id="v", model_id="m").synthesize("hello")
        self.network_attempts.clear()


class AiCallTestBase(AiTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(username="ai_owner")
        self.entity = Entity.objects.create(code="AI1", name_ar="فرع الذكاء")
        self.provider = MockElevenLabsTTS(voice_id="test-voice", model_id="eleven_multilingual_v2")
        for patcher in (mock.patch.dict(caps.CAPABILITIES["ai_voice_summary"], {"available": True}),
                        mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: lambda: self.provider})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.switch_capability(True)

    def switch_capability(self, enabled):
        FeatureFlag.objects.update_or_create(code=caps.flag_code("ai_voice_summary"), defaults={"name": "Voice summary", "enabled": enabled})

    def speak(self, text="Sales today: 3 invoices.", **kwargs):
        return services.text_to_speech(text, feature="voice_summary", user=self.user, entity=self.entity, **kwargs)

    def rows(self, action=None):
        rows = AuditLog.objects.filter(module="ai")
        return rows.filter(action=action) if action else rows

    def assertRefused(self, message, refusal):
        before = len(self.provider.calls)
        with self.assertRaisesMessage(ValidationError, message):
            self.speak()
        self.assertEqual(len(self.provider.calls), before, "a refused call must not reach the provider")
        row = self.rows(services.CALL_REFUSED).first()
        self.assertEqual((row.after_data["result"], row.after_data["refusal"]), ("refused", refusal))
        return row


class CallTests(AiCallTestBase):
    def test_a_call_returns_audio_and_writes_one_audit_row_without_the_text(self):
        speech = self.speak(SENTINEL)
        self.assertEqual((speech.content_type, speech.size), ("audio/mpeg", len(SILENT_MP3)))
        self.assertEqual(self.provider.calls[0]["body"]["text"], SENTINEL)  # the provider got it ...
        row = self.rows().get()  # ... and the audit row did not
        self.assertEqual((row.event_type, row.action, row.actor, row.object_type, row.object_id),
                         (AuditEventType.EXPORT, services.CALL_OK, self.user, "entities.Entity", str(self.entity.pk)))
        data = row.after_data
        self.assertEqual({key: data[key] for key in ("feature", "provider", "mode", "entity", "request_chars", "request_bytes", "result", "refusal", "response_bytes")},
                         {"feature": "voice_summary", "provider": "elevenlabs_tts_mock", "mode": "mock", "entity": self.entity.pk,
                          "request_chars": len(SENTINEL), "request_bytes": len(SENTINEL.encode()), "result": "ok", "refusal": "", "response_bytes": 1440})
        self.assertIsInstance(data["duration_ms"], int)
        self.assertGreaterEqual(data["duration_ms"], 0)
        stored = " ".join((row.reason, row.object_type, row.object_id, row.user_agent, json.dumps(row.after_data, ensure_ascii=False), json.dumps(row.before_data)))
        for fragment in ("Mona", "Hassan", "01001234567", "98765.43", "98765"):
            self.assertNotIn(fragment, stored)

    def test_no_entity_is_recorded_as_none(self):
        services.text_to_speech("hello", feature="voice_summary")
        row = self.rows().get()
        self.assertEqual((row.actor, row.object_type, row.object_id, row.after_data["entity"]), (None, "", "", None))

    def test_unknown_feature_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            services.text_to_speech("hello", feature="nope")
        self.assertFalse(self.rows().exists())

    def test_capability_off_is_refused(self):
        self.switch_capability(False)
        self.assertRefused("This AI feature is not switched on.", services.CAPABILITY_OFF)

    def test_kill_switch_refuses_everything(self):
        services.set_disabled(True)
        self.assertRefused("AI features are switched off for this company.", services.DISABLED)
        services.set_disabled(False)
        self.assertEqual(self.speak().content_type, "audio/mpeg")

    def test_kill_switch_comes_before_the_capability(self):
        services.set_disabled(True)
        self.switch_capability(False)
        self.assertRefused("AI features are switched off for this company.", services.DISABLED)

    def test_not_connected_is_refused(self):
        with mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: lambda: None}):
            self.assertRefused("The AI service is not connected on this server.", services.NOT_CONNECTED)

    def test_off_and_half_configured_live_reach_no_provider(self):
        with mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: providers._elevenlabs_tts}):  # the real registry
            for settings in ({"AI_MODE": "off"}, {"AI_MODE": "live", "ELEVENLABS_API_KEY": ""}):
                with self.subTest(**settings), override_settings(**settings), \
                        self.assertRaisesMessage(ValidationError, "The AI service is not connected on this server."):
                    self.speak()
        self.assertEqual(self.rows(services.CALL_REFUSED).count(), 2)

    def test_empty_or_too_long_text_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "There is no text to read aloud."):
            self.speak("   ")
        with self.assertRaisesMessage(ValidationError, "The text is too long to read aloud (at most 5000 characters)."):
            self.speak("x" * 5001)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(list(self.rows().values_list("after_data__refusal", flat=True)), [services.INVALID, services.INVALID])

    def test_provider_error_is_audited_and_counts_toward_the_cap(self):
        self.provider.fail_with = "ElevenLabs answered 500."
        with self.assertRaisesMessage(ValidationError, "The AI service did not answer. Try again later."):
            self.speak(SENTINEL)
        row = self.rows().get()
        self.assertEqual((row.action, row.after_data["result"]), (services.CALL_ERROR, "error"))
        self.assertNotIn("Mona", json.dumps(row.after_data))
        self.assertEqual(services.calls_today(), 1)

    @override_settings(**LIVE_KEYS)
    def test_live_mode_end_to_end_with_urllib_mocked(self):
        with mock.patch.dict(providers.PROVIDERS, {providers.ELEVENLABS_TTS: providers._elevenlabs_tts}), \
                mock.patch(OPEN, return_value=FakeResponse()) as urlopen:
            speech = self.speak("hello")
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://api.elevenlabs.io/v1/text-to-speech/test-voice")
        self.assertEqual(urlopen.call_args.args[0].get_header("Xi-api-key"), "test-key-not-real")
        self.assertEqual(speech.audio, SILENT_MP3)
        data = self.rows().get().after_data
        self.assertEqual((data["provider"], data["mode"]), ("elevenlabs_tts", "live"))
        self.assertNotIn("test-key-not-real", json.dumps(data))

    def test_any_exception_from_the_provider_is_audited_then_raised(self):
        with mock.patch.object(self.provider, "synthesize", side_effect=RuntimeError("boom")), self.assertRaisesMessage(RuntimeError, "boom"):
            self.speak(SENTINEL)
        row = self.rows().get()
        self.assertEqual((row.action, row.after_data["result"], row.after_data["error_status"]), (services.CALL_ERROR, "error", None))
        self.assertIsInstance(row.after_data["duration_ms"], int)
        self.assertNotIn("Mona", json.dumps(row.after_data))
        self.assertEqual(services.calls_today(), 1)

    def test_a_call_inside_a_transaction_is_refused_before_anything(self):
        with transaction.atomic(), self.assertRaisesMessage(services.AiCallInTransaction, "must not run inside transaction.atomic()"):
            self.speak()
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(self.rows().exists())
        self.assertFalse(services.in_outer_transaction())  # the test case's own wrapping does not count
        self.assertEqual(self.speak().content_type, "audio/mpeg")

    @override_settings(AI_MODE="live")
    def test_the_mode_recorded_is_the_provider_actually_used(self):
        self.speak()  # the registry hands back the mock although AI_MODE says live
        self.assertEqual(self.rows().get().after_data["mode"], "mock")


class CapabilityRegistrationTests(AiTestCase):
    def test_voice_summary_is_registered_off_and_cannot_be_switched_on_in_a0(self):
        self.assertIn("ai_voice_summary", caps.CAPABILITY_SLUGS)
        self.assertFalse(caps.is_available("ai_voice_summary"))
        FeatureFlag.objects.create(code=caps.flag_code("ai_voice_summary"), name="Voice summary", enabled=True)
        self.assertNotIn("ai_voice_summary", caps.enabled_capabilities())  # even with its flag row set on
        self.assertFalse(any(caps.fits(activity, "ai_voice_summary") for activity in ("commercial", "services", "medical")))
        self.assertEqual(services.FEATURES["voice_summary"].capability, "ai_voice_summary")

    def test_a_call_is_refused_while_the_capability_is_unavailable(self):
        FeatureFlag.objects.create(code=caps.flag_code("ai_voice_summary"), name="Voice summary", enabled=True)
        with self.assertRaisesMessage(ValidationError, "This AI feature is not switched on."):
            services.text_to_speech("hello", feature="voice_summary")
        self.assertEqual(AuditLog.objects.get(module="ai").after_data["refusal"], services.CAPABILITY_OFF)


class DailyCapTests(AiCallTestBase):
    def test_default_cap_is_fifty_and_bad_values_fall_back(self):
        self.assertEqual(services.daily_cap(), 50)
        services.set_daily_cap(7)
        self.assertEqual(services.daily_cap(), 7)
        from settings_core.models import SystemSetting

        SystemSetting.objects.filter(key=services.DAILY_CAP_KEY).update(value="lots")
        self.assertEqual(services.daily_cap(), 50)
        SystemSetting.objects.filter(key=services.DAILY_CAP_KEY).update(value="-3")
        self.assertEqual(services.daily_cap(), 0)

    def test_the_server_bounds_the_cap_from_above(self):
        services.set_daily_cap(100000)
        self.assertEqual(services.daily_cap(), 500)  # AI_DAILY_CALL_CAP_MAX's default
        with override_settings(AI_DAILY_CALL_CAP_MAX=20):
            self.assertEqual(services.daily_cap(), 20)
            services.set_daily_cap(5)
            self.assertEqual(services.daily_cap(), 5)
        with override_settings(AI_DAILY_CALL_CAP_MAX=10):
            from settings_core.models import SystemSetting

            SystemSetting.objects.filter(key=services.DAILY_CAP_KEY).delete()
            self.assertEqual(services.daily_cap(), 10)  # the default of 50 is bounded too

    def test_a_call_still_in_flight_counts_toward_the_cap(self):
        services.set_daily_cap(1)
        seen = {}
        real = self.provider.synthesize

        def in_flight(text, voice_settings=None):
            seen["count"] = services.calls_today()
            seen["action"] = self.rows().exclude(action=services.CALL_REFUSED).get().action
            with self.assertRaisesMessage(ValidationError, "Today's AI call limit is reached (1 calls)."):
                self.speak()  # a second call while the first is still talking to the provider
            return real(text, voice_settings)

        with mock.patch.object(self.provider, "synthesize", side_effect=in_flight):
            self.speak()
        self.assertEqual(seen, {"count": 1, "action": services.CALL_STARTED})
        self.assertEqual(sorted(self.rows().values_list("action", flat=True)), [services.CALL_OK, services.CALL_REFUSED])
        self.assertEqual(services.calls_today(), 1)

    def test_check_and_reserve_run_under_the_lock_row(self):
        from django.db.models.query import QuerySet

        steps, real = [], QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            steps.append(("lock", queryset.model._meta.label))
            return real(queryset, *args, **kwargs)

        real_count = services.calls_today

        def counted(now=None):
            steps.append(("count", transaction.get_connection().in_atomic_block))
            return real_count(now)

        with mock.patch.object(QuerySet, "select_for_update", spy), mock.patch.object(services, "calls_today", counted):
            self.speak()
        self.assertEqual(steps, [("lock", "settings_core.SystemSetting"), ("count", True)])
        from settings_core.models import SystemSetting

        lock = SystemSetting.objects.get(key=services.LOCK_KEY)
        self.assertFalse(lock.active)  # not a setting: hidden from the settings screen
        self.assertEqual(lock.value, "")

    def test_the_lock_is_a_real_row_lock_on_postgres(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        if connection.vendor != "postgresql":
            self.skipTest("SQLite has no row locks (and serialises writers anyway); CI runs this on PostgreSQL.")
        with CaptureQueriesContext(connection) as queries:
            self.speak()
        locks = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"]]
        self.assertEqual(len(locks), 1)
        self.assertIn("settings_core_systemsetting", locks[0])
        lock_at = next(index for index, query in enumerate(queries) if "FOR UPDATE" in query["sql"])
        started_at = next(index for index, query in enumerate(queries) if "INSERT" in query["sql"] and "audit_auditlog" in query["sql"])
        self.assertLess(lock_at, started_at)

    def test_cap_reached_is_refused_and_refusals_do_not_count(self):
        services.set_daily_cap(2)
        self.speak()
        self.speak()
        self.assertRefused("Today's AI call limit is reached (2 calls). Try again tomorrow.", services.DAILY_CAP)
        self.assertRefused("Today's AI call limit is reached (2 calls). Try again tomorrow.", services.DAILY_CAP)
        self.assertEqual(services.calls_today(), 2)
        self.assertEqual(len(self.provider.calls), 2)

    def test_cap_zero_refuses_the_first_call(self):
        services.set_daily_cap(0)
        self.assertRefused("Today's AI call limit is reached (0 calls). Try again tomorrow.", services.DAILY_CAP)

    def test_the_count_resets_on_the_next_cairo_day(self):
        services.set_daily_cap(1)
        self.speak()
        self.assertRefused("Today's AI call limit is reached (1 calls). Try again tomorrow.", services.DAILY_CAP)
        tomorrow = timezone.now() + timedelta(days=1)
        with mock.patch("django.utils.timezone.now", return_value=tomorrow):
            self.assertEqual(services.calls_today(), 0)
            self.assertEqual(self.speak().content_type, "audio/mpeg")
            self.assertEqual(services.calls_today(), 1)

    def test_the_day_is_cairo_not_utc(self):
        # November is winter time in Cairo (UTC+2): the Cairo day of the 10th starts at 22:00 UTC on the 9th.
        def cairo(day, hour, minute):
            return datetime(2026, 11, day, hour, minute, tzinfo=CAIRO)

        late = AuditLog.objects.create(event_type=AuditEventType.EXPORT, module="ai", action=services.CALL_OK)
        AuditLog.objects.filter(pk=late.pk).update(created_at=cairo(9, 23, 30))  # 21:30 UTC on the 9th
        early = AuditLog.objects.create(event_type=AuditEventType.EXPORT, module="ai", action=services.CALL_ERROR)
        AuditLog.objects.filter(pk=early.pk).update(created_at=cairo(10, 0, 30))  # 22:30 UTC, still the 9th in UTC
        refused = AuditLog.objects.create(event_type=AuditEventType.EXPORT, module="ai", action=services.CALL_REFUSED)
        AuditLog.objects.filter(pk=refused.pk).update(created_at=cairo(9, 12, 0))
        self.assertEqual(services.calls_today(cairo(9, 23, 45)), 1)  # the late call only; the refusal never counts
        unfinished = AuditLog.objects.create(event_type=AuditEventType.EXPORT, module="ai", action=services.CALL_STARTED)
        AuditLog.objects.filter(pk=unfinished.pk).update(created_at=cairo(9, 13, 0))
        self.assertEqual(services.calls_today(cairo(9, 23, 45)), 2)  # a call that never finished keeps counting
        self.assertEqual(services.calls_today(cairo(10, 0, 45)), 1)  # a new Cairo day on the same UTC date: the early call only
        self.assertEqual(services.calls_today(cairo(11, 9, 0)), 0)
        start, end = services.cairo_day_bounds(cairo(10, 0, 45))
        self.assertEqual((start, end), (cairo(10, 0, 0), cairo(11, 0, 0)))


class EveryAiTestIsGuardedTests(AiSimpleTestCase):
    def test_every_test_case_in_this_module_uses_the_no_network_base(self):
        import inspect
        import sys
        import unittest

        from .no_network import NoNetworkMixin

        cases = [obj for obj in vars(sys.modules[__name__]).values() if inspect.isclass(obj) and issubclass(obj, unittest.TestCase)]
        self.assertGreater(len(cases), 5)
        self.assertEqual([case.__name__ for case in cases if not issubclass(case, NoNetworkMixin)], [])


class MessageTests(AiSimpleTestCase):
    def test_every_refusal_reads_in_arabic(self):
        for english in ("AI features are switched off for this company.", "This AI feature is not switched on.",
                        "The AI service is not connected on this server.", "There is no text to read aloud.",
                        "The AI service did not answer. Try again later."):
            with self.subTest(english=english):
                self.assertNotEqual(translate(english), english)
                self.assertEqual(translate(english, "en"), english)
        self.assertEqual(translate("Today's AI call limit is reached (50 calls). Try again tomorrow."),
                         "وصلت لحد مكالمات الذكاء الاصطناعي النهارده (50 مكالمة). جرّب تاني بكرة.")
        self.assertEqual(translate("The text is too long to read aloud (at most 5000 characters)."), "النص طويل أوي على القراية بالصوت (الحد 5000 حرف).")
