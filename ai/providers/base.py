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


# ---- A2 (HG-080): a question routed to one read-only tool ----

#: What a routing call can come back as. Only ``TOOL`` carries a tool to run; the rest are answered with a fixed message.
TOOL, REFUSAL, NO_TOOL, TRUNCATED, INVALID_TOOL, INVALID_ARGUMENTS = (
    "tool", "refusal", "no_tool", "truncated", "invalid_tool", "invalid_arguments")


@dataclass(frozen=True)
class Routing:
    """The model's choice, as read from its answer: ``status`` and, for ``TOOL``, one tool name and its arguments.

    Nothing else from the answer is kept: no text the model wrote is ever shown or stored. ``tool`` and ``arguments``
    are untrusted until ``ai.services`` has checked them against the tools it sent.
    """

    status: str
    tool: str = ""
    arguments: dict = None
    stop_reason: str = ""

    @property
    def routed(self):
        return self.status == TOOL


class QuestionRouter:
    """Picks one tool for a question. ``live`` is False for the mock."""

    code = ""
    kind = "router"
    live = False

    def route(self, question, *, system, tools):  # pragma: no cover - the interface
        raise NotImplementedError
