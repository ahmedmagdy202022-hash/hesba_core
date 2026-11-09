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
"""

from .base import NO_TOOL, REFUSAL, TOOL, TRUNCATED, ProviderError, QuestionRouter, Routing

DEFAULT_MODEL = "claude-opus-5-5"
BASE_URL = "https://api.anthropic.com"
#: Seconds the SDK waits for one attempt, and how many times it retries (a 429/5xx or a dropped connection).
TIMEOUT = 20.0
MAX_RETRIES = 1
MAX_TOKENS = 1024
EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


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


class ClaudeRouter(QuestionRouter):
    code = "claude_router"
    live = True

    def __init__(self, api_key, model=DEFAULT_MODEL):
        self.api_key = str(api_key or "")
        self.model = str(model or DEFAULT_MODEL)
        self._client = None

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

    # ---- transport (the only SDK access) ----

    def _create(self, request):
        import anthropic

        if self._client is None:
            self._client = new_client(self.api_key)
        try:
            return self._client.beta.messages.create(**request)
        except anthropic.APIStatusError as exc:  # a 4xx/5xx; its text carries the response body, so only the code is kept
            raise ProviderError(f"Anthropic answered {exc.status_code}.", status=exc.status_code) from None
        except anthropic.APIConnectionError:  # unreachable, or timed out
            raise ProviderError("Anthropic could not be reached.") from None
        except anthropic.AnthropicError:  # an answer the SDK could not read
            raise ProviderError("Anthropic gave an unreadable answer.") from None

    # ---- the one call ----

    def route(self, question, *, system, tools):
        return parse_response(self._create(self.build_request(question, system=system, tools=tools)))
