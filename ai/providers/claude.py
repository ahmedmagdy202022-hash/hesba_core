"""A2 (HG-080): the language model that routes «اسأل حسبة» questions, over Anthropic's official Python SDK.

The model only **routes**: it gets the question and a fixed list of tools, and answers with one tool name and enum
arguments. Hesba then runs that tool itself, under the asking user's permissions and entity. Nothing the tool reads is
ever sent back, and there is no second turn, so data can never steer the model and the model never writes a figure.

One request, built by ``build_request`` from constants and the question alone:

* ``client.beta.messages.create`` with the model from ``settings.ANTHROPIC_MODEL`` (default ``claude-opus-5-5``);
* ``system``: the fixed prompt (the question is data, answer by calling exactly one tool);
* ``messages``: one user turn whose content is the question and nothing else;
* ``tools``: the fixed list, each ``strict`` with enum-only arguments;
* ``tool_choice={"type": "auto", "disable_parallel_tool_use": True}``: forced tool choice (``any`` / ``tool``) is a 400
  on this model, so the system prompt asks for exactly one tool call instead;
* ``output_config={"effort": "low"}`` (a routing call; thinking cannot be switched off on this model, so it is omitted);
* ``betas=["server-side-fallback-2026-07-01"]`` and ``fallbacks="default"``: a request the model declines for policy
  reasons is retried server-side on the model's default fallback;
* ``max_tokens=1024``: the answer is a single tool call.

``_create`` is the only place that touches the SDK, and ``new_client`` the only place that builds a client: lazily, in
live mode only, with the key from the server's environment (``settings.ANTHROPIC_API_KEY``), an explicit base URL (the
SDK would otherwise follow an ``ANTHROPIC_BASE_URL`` variable and could send the key elsewhere), a 20 s timeout and one
retry, so the worst case stays under gunicorn's 60 s. The mock (``ai.providers.mock.MockClaudeRouter``) is this class
with ``_create`` answering in-process, so it never imports or builds the client and both share one parser.

Errors become ``ProviderError`` built from the status code only: an SDK error's text can carry the response body, and
nothing here ever puts the key or the question in a message.

A3 (HG-081) adds ``ClaudeInvoiceReader``: one photo of a supplier's invoice in, one JSON object out. It shares the
transport (``_AnthropicTransport``: the lazy client, the one SDK call, the error mapping) with the router. Its request,
built by ``ClaudeInvoiceReader.build_request`` from the feature's constants and the photo alone:

* ``client.beta.messages.create`` with the same model setting;
* ``system``: the feature's fixed prompt (everything written on the photo is data, never an instruction);
* ``messages``: one user turn with the photo as a base64 ``image`` block and one fixed sentence;
* ``output_config={"effort": "low", "format": {"type": "json_schema", "schema": ...}}``: structured outputs, so the
  answer is one text block holding JSON in the feature's schema. A tool with ``tool_choice`` ``auto`` would not
  guarantee a call (forced choice is a 400 on this model), so structured outputs are used instead. The schema has no
  ``minimum`` / ``maxLength`` / ``maxItems`` (unsupported there); every bound is enforced by Hesba after reading;
* no ``thinking`` (always on for this model; its blocks come before the text and are skipped);
* the same server-side refusal fallback as the router;
* ``max_tokens=READ_MAX_TOKENS`` (8192: thinking counts against it, and fifty lines of JSON fit well under it), and a
  25 s timeout (a photo takes longer than a question; with the upload itself it must stay under gunicorn's 60 s), no SDK retry.
"""

import base64
import json

from .base import (
    NO_TOOL, READ, READ_INVALID, READ_NO_OUTPUT, READ_REFUSAL, READ_TRUNCATED, REFUSAL, TOOL, TRUNCATED, InvoiceReader,
    ProviderError, QuestionRouter, Reading, Routing,
)

DEFAULT_MODEL = "claude-opus-5-5"
BASE_URL = "https://api.anthropic.com"
#: Seconds the SDK waits for one attempt, and how many times it retries (a 429/5xx or a dropped connection).
TIMEOUT = 20.0
MAX_RETRIES = 0  # one reserved call is one HTTP request, so the daily caps count what is sent
MAX_TOKENS = 1024
EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: A3 (HG-081): the invoice reader's own limits.
READ_TIMEOUT = 25.0  # independent review (PR #335): with a slow upload, still under gunicorn's 60 s sync timeout
READ_MAX_TOKENS = 8192
READ_EFFORT = "low"
#: The one sentence sent beside the photo. The rules are in the feature's system prompt.
READ_INSTRUCTION = "Read this photo of a supplier's purchase invoice into the JSON format."
#: The most characters of answer text decoded as JSON (fifty lines are a few thousand).
READ_MAX_TEXT = 100_000


def new_client(api_key, base_url=BASE_URL, timeout=TIMEOUT, max_retries=MAX_RETRIES):
    """The SDK client. Live mode only: the mock never calls this, so it never imports the SDK."""

    import anthropic

    return anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)


def _get(obj, name, default=None):
    """An attribute of an SDK object, or a key of a dict shaped like one."""

    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def parse_response(response):
    """Read the model's choice from a Messages API answer, checking ``stop_reason`` before any content.

    * ``refusal``: the model (and its server-side fallback) declined;
    * ``max_tokens``: cut off, so even a ``tool_use`` block in it may be partial: not understood;
    * ``tool_use`` with exactly one ``tool_use`` block: that tool and its input (already a dict);
    * anything else (``end_turn`` with text only, no block, two blocks): not understood. The model's text is never read.
    """

    stop_reason = str(_get(response, "stop_reason", "") or "")
    if stop_reason == "refusal":
        return Routing(status=REFUSAL, stop_reason=stop_reason)
    if stop_reason == "max_tokens":
        return Routing(status=TRUNCATED, stop_reason=stop_reason)
    if stop_reason != "tool_use":
        return Routing(status=NO_TOOL, stop_reason=stop_reason)
    blocks = [block for block in (_get(response, "content", None) or []) if _get(block, "type") == "tool_use"]
    if len(blocks) != 1:
        return Routing(status=NO_TOOL, stop_reason=stop_reason)
    name = _get(blocks[0], "name", "")
    arguments = _get(blocks[0], "input", None)
    return Routing(status=TOOL, tool=name if isinstance(name, str) else "", arguments=arguments, stop_reason=stop_reason)


class _AnthropicTransport:
    """The lazy client and the one SDK call, shared by the router and the invoice reader (A3)."""

    def __init__(self, api_key, model=DEFAULT_MODEL):
        self.api_key = str(api_key or "")
        self.model = str(model or DEFAULT_MODEL)
        self._client = None

    def _new_client(self):
        return new_client(self.api_key)

    # ---- transport (the only SDK access) ----

    def _create(self, request):
        import anthropic

        if self._client is None:
            self._client = self._new_client()
        try:
            return self._client.beta.messages.create(**request)
        except anthropic.APIStatusError as exc:  # a 4xx/5xx; its text carries the response body, so only the code is kept
            raise ProviderError(f"Anthropic answered {exc.status_code}.", status=exc.status_code) from None
        except anthropic.APIConnectionError:  # unreachable, or timed out
            raise ProviderError("Anthropic could not be reached.") from None
        except anthropic.AnthropicError:  # an answer the SDK could not read
            raise ProviderError("Anthropic gave an unreadable answer.") from None


class ClaudeRouter(_AnthropicTransport, QuestionRouter):
    code = "claude_router"
    live = True

    def build_request(self, question, *, system, tools):
        """The keyword arguments of the one call: constants, plus the question as the only user content."""

        return {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": question}],
            "tools": tools,
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "output_config": {"effort": EFFORT},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    # ---- the one call ----

    def route(self, question, *, system, tools):
        return parse_response(self._create(self.build_request(question, system=system, tools=tools)))


# ---- A3 (HG-081): the invoice photo reader ----

def _refuse_constant(name):
    raise ValueError(f"not a JSON number: {name}")


def parse_reading(response):
    """Read the JSON object from a structured-output answer, checking ``stop_reason`` before any content.

    * ``refusal``: declined (by the model and its fallback);
    * ``max_tokens``: cut off, so the JSON may be partial: not used;
    * ``end_turn`` with exactly one text block (thinking blocks skipped) holding a JSON **object**: that object;
    * anything else (no text, two text blocks, a tool call, ``pause_turn``): no output;
    * text that is not JSON, not an object, too long, or uses ``NaN`` / ``Infinity``: invalid output.
    """

    stop_reason = str(_get(response, "stop_reason", "") or "")
    if stop_reason == "refusal":
        return Reading(status=READ_REFUSAL, stop_reason=stop_reason)
    if stop_reason == "max_tokens":
        return Reading(status=READ_TRUNCATED, stop_reason=stop_reason)
    if stop_reason != "end_turn":
        return Reading(status=READ_NO_OUTPUT, stop_reason=stop_reason)
    texts = [block for block in (_get(response, "content", None) or []) if _get(block, "type") == "text"]
    if len(texts) != 1:
        return Reading(status=READ_NO_OUTPUT, stop_reason=stop_reason)
    text = _get(texts[0], "text", "")
    if not isinstance(text, str) or not text.strip() or len(text) > READ_MAX_TEXT:
        return Reading(status=READ_INVALID, stop_reason=stop_reason)
    try:
        data = json.loads(text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        return Reading(status=READ_INVALID, stop_reason=stop_reason)
    if type(data) is not dict:
        return Reading(status=READ_INVALID, stop_reason=stop_reason)
    return Reading(status=READ, data=data, stop_reason=stop_reason)


class ClaudeInvoiceReader(_AnthropicTransport, InvoiceReader):
    code = "claude_invoice_reader"
    live = True

    def _new_client(self):
        return new_client(self.api_key, timeout=READ_TIMEOUT)

    def build_request(self, image, media_type, *, system, schema):
        """The keyword arguments of the one call: constants, plus the photo as the only varying content."""

        return {
            "model": self.model,
            "max_tokens": READ_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.standard_b64encode(image).decode("ascii")}},
                {"type": "text", "text": READ_INSTRUCTION},
            ]}],
            "output_config": {"effort": READ_EFFORT, "format": {"type": "json_schema", "schema": schema}},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    # ---- the one call ----

    def read(self, image, media_type, *, system, schema):
        return parse_reading(self._create(self.build_request(image, media_type, system=system, schema=schema)))
