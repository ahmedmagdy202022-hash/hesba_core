"""HG-078 (A0): ElevenLabs text-to-speech, over its public REST API with the standard library (no SDK).

``POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`` with the key in the ``xi-api-key`` header and a JSON
body ``{"text", "model_id", "voice_settings"?}``; the answer is the audio itself (``audio/mpeg``).

Every request goes through ``ElevenLabsTTS._http``, the one place that opens a socket. The mock
(``ai.providers.mock.MockElevenLabsTTS``) is this same class with ``_http`` answering in-process, so the request a test
inspects is the one the live client would send. The key comes from the server's environment only
(``settings.ELEVENLABS_API_KEY``, read with ``config()``), never from a model.

Two limits keep a slow or misdirected answer from hurting the server:

* **No redirects.** The opener refuses every 3xx, so the ``xi-api-key`` header is never re-sent to another address;
  the refusal is ``ProviderError("ElevenLabs answered 3xx.")``.
* **A deadline on the body.** Each socket operation waits at most ``TIMEOUT`` seconds. The body is read with
  ``read1``, which does at most one socket read and returns whatever arrived (``read(n)`` would block until it had
  ``n`` bytes, so a slow drip could outlive any deadline). After each read the elapsed time is checked against
  ``DEADLINE``. The clock starts before connecting, but the connect and the status line and headers are bounded only
  by the per-operation ``TIMEOUT``, not by the deadline: they finish before the body loop starts. For an answer whose
  headers arrive promptly the worst case is about ``DEADLINE + TIMEOUT`` (30 s), under gunicorn's 60 s worker timeout.
"""

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import ProviderError, Speech, TextToSpeechProvider

BASE_URL = "https://api.elevenlabs.io"
#: Seconds one socket operation may wait (the connect, and each read of the body).
TIMEOUT = 10
#: Seconds from opening the connection to the last byte of audio, checked after each read of the body.
DEADLINE = 20
CHUNK_BYTES = 64 * 1024
#: An answer larger than this is refused rather than held in memory (a day's summary is well under 1 MB).
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow no redirect: urllib then raises the 3xx as an ``HTTPError``, which becomes a ``ProviderError``."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener(*handlers):
    return urllib.request.build_opener(_RefuseRedirects, *handlers)


_OPENER = _build_opener()


def _open(request, timeout):
    return _OPENER.open(request, timeout=timeout)


class ElevenLabsTTS(TextToSpeechProvider):
    code = "elevenlabs_tts"
    live = True

    def __init__(self, api_key, voice_id, model_id, base_url=BASE_URL, timeout=TIMEOUT, deadline=DEADLINE):
        self.api_key = str(api_key or "")
        self.voice_id = str(voice_id or "")
        self.model_id = str(model_id or "")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.deadline = deadline

    def url(self):
        return f"{self.base_url}/v1/text-to-speech/{urllib.parse.quote(self.voice_id, safe='')}"

    def build_request(self, text, voice_settings=None):
        body = {"text": text, "model_id": self.model_id}
        if voice_settings:
            body["voice_settings"] = dict(voice_settings)
        return urllib.request.Request(
            self.url(), data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        )

    # ---- transport (the only network access) ----

    def _http(self, request):
        """Send ``request``; return ``(content_type, body bytes)``. Errors never quote the key, the text or the answer."""

        deadline = time.monotonic() + self.deadline
        try:
            with _open(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "") if response.headers else ""
                chunks, total = [], 0
                while True:
                    chunk = response.read1(CHUNK_BYTES)  # at most one socket read: a drip cannot outlive the deadline
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        raise ProviderError("ElevenLabs answered with more audio than allowed.")
                    if time.monotonic() > deadline:
                        raise ProviderError("ElevenLabs took too long to answer.")
                    chunks.append(chunk)
        except urllib.error.HTTPError as exc:  # a 4xx/5xx, or a refused 3xx redirect
            raise ProviderError(f"ElevenLabs answered {exc.code}.", status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:  # HTTPException: a cut-off answer (IncompleteRead)
            raise ProviderError("ElevenLabs could not be reached.") from exc
        return content_type, b"".join(chunks)

    # ---- the one call ----

    def synthesize(self, text, voice_settings=None):
        content_type, audio = self._http(self.build_request(text, voice_settings))
        content_type = (content_type or "").split(";")[0].strip().lower()
        if not audio or not content_type.startswith("audio/"):
            raise ProviderError("ElevenLabs gave no audio.")
        return Speech(audio=audio, content_type=content_type)
