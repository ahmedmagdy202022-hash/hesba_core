"""HG-078 (A0): ElevenLabs with its network swapped for an in-process answer (development and tests).

``MockElevenLabsTTS`` *is* ``ElevenLabsTTS``: only ``_http`` differs, so the request it records is the one the live
client builds. Nothing here opens a socket. The answer is always the same small, valid, silent MP3.

A2 (HG-080): ``MockClaudeRouter`` *is* ``ClaudeRouter`` with only ``_create`` swapped: it routes a question to a tool
by its words, deterministically, and answers in the Messages API's shape. It never imports or builds the SDK client.

A3 (HG-081): ``MockClaudeInvoiceReader`` *is* ``ClaudeInvoiceReader`` with only ``_create`` swapped: whatever the photo,
it answers with the approved artboard's sample reading, as a structured-output answer. It never builds the SDK client.
"""

import json
import re
import unicodedata

from .base import ProviderError
from .claude import ClaudeInvoiceReader, ClaudeRouter
from .elevenlabs import ElevenLabsTTS

#: One MPEG-1 Layer III frame: 32 kbit/s, 32 kHz, mono, no CRC (header FF FB 18 C0), zero side information and zero
#: main data, which decodes to 1152 samples of silence. 144 bytes per frame; ten frames are 0.36 s.
SILENT_FRAME = bytes((0xFF, 0xFB, 0x18, 0xC0)) + bytes(140)
SILENT_MP3 = SILENT_FRAME * 10


class MockElevenLabsTTS(ElevenLabsTTS):
    code = "elevenlabs_tts_mock"
    live = False

    def __init__(self, voice_id, model_id, fail_with=None):
        super().__init__(api_key="mock-api-key", voice_id=voice_id, model_id=model_id,
                         base_url="https://api.elevenlabs.invalid")
        self.fail_with = fail_with  # a test can make the "provider" refuse
        self.calls = []

    def _http(self, request):
        self.calls.append({"method": request.get_method(), "url": request.full_url,
                           "body": json.loads(request.data.decode("utf-8"))})
        if self.fail_with:
            raise ProviderError(self.fail_with)
        return "audio/mpeg", SILENT_MP3


# ---- A2 (HG-080): the question router, routing on keywords ----

#: Which tool a question asks for, by its words after ``normalise``, first match wins (Arabic and English). The order
#: matters: «كام ربحت من المبيعات» is profit, «مين عليه فلوس» is aging, «المخزون» is stock and not the cashbox.
TOOL_WORDS = (
    ("profit_total", ("ربح", "ارباح", "مكسب", "كسبت", "profit")),
    ("top_debtors", ("مين عليه", "عليه فلوس", "عليهم فلوس", "مديون", "اعمار الديون", " owes", " owe ", "debtor", "debt")),
    ("collections_total", ("تحصيل", "حصلت", "اتحصل", "collect", "receipts")),
    ("low_stock", ("ناقص", "قرب يخلص", "قربت تخلص", "يخلص", "مخزون", "نفد", "stock", "running low", "running out")),
    ("cashbox_balances", ("خزنه", " الخزن ", "خزينه", "cashbox", "cash box", " cash", " till")),
    ("appointments_today", ("مواعيد", "ميعاد", "حجوزات", "appointment", "booking")),
    ("top_items", ("اكتر صنف", "اكثر صنف", "الاكثر مبيع", "الاكتر مبيع", "اكتر حاجه", "best sell", "best-sell", "top item", "top sell", "best item")),
    ("invoice_count", ("كام فاتوره", "عدد الفواتير", "فواتير", "invoice")),
    ("sales_total", ("مبيعات", "بعت", "بيع", "sales", " sold", "revenue")),
)
#: The period, by its words, first match wins («الشهر اللي فات» before «الشهر»). No period word means today.
PERIOD_WORDS = (
    ("yesterday", ("امبارح", "امس", "yesterday")),
    ("last_month", ("الشهر اللي فات", "الشهر الماضي", "الشهر السابق", "last month", "previous month")),
    ("this_week", ("الاسبوع", "week")),
    ("this_month", ("الشهر", "month")),
    ("today", ("النهارده", "اليوم", "today")),
)
PERIOD_TOOLS = ("sales_total", "invoice_count", "collections_total", "profit_total", "top_items")
#: Each tool that takes a ``limit``, and the one it uses when the question names none.
LIMIT_TOOLS = {"top_debtors": 1, "low_stock": 5, "top_items": 3}
_DIACRITICS = re.compile("[ً-ْـ]")  # harakat and tatweel


def normalise(text):
    """Lower case, Arabic letters folded (أ إ آ → ا, ى → ي, ة → ه), harakat and tatweel dropped, Arabic digits read,
    spaces collapsed, with one space at each end so a word can be matched at its start."""

    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = _DIACRITICS.sub("", text)
    text = text.translate(str.maketrans("أإآىة٠١٢٣٤٥٦٧٨٩", "ااايه0123456789"))
    return f" {' '.join(text.split())} "


def keyword_route(question):
    """``(tool, arguments)`` for ``question`` by its words, or ``(None, None)`` when no tool's words are in it."""

    text = normalise(question)
    tool = next((name for name, words in TOOL_WORDS if any(word in text for word in words)), None)
    if tool is None:
        return None, None
    arguments = {}
    if tool in PERIOD_TOOLS:
        arguments["period"] = next((period for period, words in PERIOD_WORDS if any(word in text for word in words)), "today")
    if tool in LIMIT_TOOLS:
        limit = next((n for n in (5, 3, 1) if re.search(rf"(?<!\d){n}(?!\d)", text)), None)
        arguments["limit"] = limit or LIMIT_TOOLS[tool]
    return tool, arguments


class MockClaudeRouter(ClaudeRouter):
    """``ClaudeRouter`` with only ``_create`` swapped. ``answer`` lets a test make the "model" say anything (a dict shaped
    like a response: a refusal, a tool that does not exist, an argument outside its enum); ``fail_with`` makes it fail."""

    code = "claude_router_mock"
    live = False

    def __init__(self, model="mock-model", answer=None, fail_with=None):
        super().__init__(api_key="mock-api-key", model=model)
        self.answer = answer
        self.fail_with = fail_with
        self.calls = []

    def _create(self, request):
        self.calls.append(request)
        if self.fail_with:
            raise ProviderError(self.fail_with)
        if self.answer is not None:
            return self.answer(request) if callable(self.answer) else self.answer
        tool, arguments = keyword_route(request["messages"][0]["content"])
        if tool is None:
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Mock: no tool matched."}]}
        return {"stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_mock", "name": tool, "input": arguments}]}


# ---- A3 (HG-081): the invoice photo reader, answering with one fixed reading ----

#: The approved artboard's sample: a car-parts supplier's invoice with three lines, the third a part the shop keeps under
#: another name (so it needs mapping), and a total written on the paper 100.00 above the sum of its lines (8,440.00).
SAMPLE_READING = {
    "supplier_name": "مؤسسة الأمل للتوريدات",
    "supplier_code": "",
    "invoice_number": "INV-5531",
    "invoice_date": "2026-11-08",
    "written_total": "8540.00",
    "lines": [
        {"item_code": "", "name": "زيت محرك 4 لتر", "quantity": "12", "unit_price": "385.00"},
        {"item_code": "", "name": "فلتر زيت تويوتا", "quantity": "20", "unit_price": "95.00"},
        {"item_code": "", "name": "تيل فرامل أمامي K-22", "quantity": "8", "unit_price": "240.00"},
    ],
}


def reading_response(data, stop_reason="end_turn"):
    """A structured-output answer in the Messages API's shape: an (empty) thinking block, then the JSON text."""

    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"stop_reason": stop_reason, "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}]}


class MockClaudeInvoiceReader(ClaudeInvoiceReader):
    """``ClaudeInvoiceReader`` with only ``_create`` swapped: it answers with ``SAMPLE_READING`` whatever the photo.
    ``answer`` lets a test make the "model" say anything (a response-shaped dict, or a callable taking the request);
    ``fail_with`` makes it fail. It never imports or builds the SDK client."""

    code = "claude_invoice_reader_mock"
    live = False

    def __init__(self, model="mock-model", answer=None, fail_with=None):
        super().__init__(api_key="mock-api-key", model=model)
        self.answer = answer
        self.fail_with = fail_with
        self.calls = []

    def _create(self, request):
        self.calls.append(request)
        if self.fail_with:
            raise ProviderError(self.fail_with)
        if self.answer is not None:
            return self.answer(request) if callable(self.answer) else self.answer
        return reading_response(SAMPLE_READING)
