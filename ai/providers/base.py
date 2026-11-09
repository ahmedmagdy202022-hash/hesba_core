"""HG-078 (A0): what every AI provider looks like to the rest of Hesba."""

from dataclasses import dataclass


class ProviderError(Exception):
    """The provider could not answer (unreachable, refused, or an unreadable answer).

    The message never carries the key, the text sent or the provider's response body.
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Speech:
    audio: bytes
    content_type: str

    @property
    def size(self):
        return len(self.audio)


class TextToSpeechProvider:
    """Turns text into audio. ``live`` is False for the mock."""

    code = ""
    kind = "tts"
    live = False

    def synthesize(self, text, voice_settings=None):  # pragma: no cover - the interface
        raise NotImplementedError
