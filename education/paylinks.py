"""E4a: pay fees by link. Entry points only: every link is made by ``payments.services.create_link``, unchanged, and every
payment is recorded by the gateway's verified callback through the S4 code. Nothing here posts, settles or moves money.

What this module adds is reading and choosing:

* ``Board``: for one request, every posted fee invoice that may still have money due, with its live link, who pays and
  what the link would ask for, loaded in a few queries (not one set per row);
* what a link asks for: ``min(what the invoice still asks, what the parent's account still owes)``, newest invoice first.
  A fee is usually collected at the desk as a plain customer payment, which does not lower an invoice's own «outstanding»,
  so asking the invoice's figure alone would offer a link for fees already paid. The figure goes through ``create_link``'s
  own ``amount`` argument, which accepts anything up to the outstanding;
* the WhatsApp message, and the bulk run.
"""

import logging
import time
from decimal import Decimal
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.utils import timezone

from config.money import money_round
from payments import services
from permissions.services import user_has_permission

log = logging.getLogger(__name__)

ZERO = Decimal("0.00")
#: One click makes at most this many links, and stops starting new ones after this many seconds. ``create_link`` calls the
#: gateway about three times (auth, order, payment key), each up to 20 s (``providers.paymob.TIMEOUT``), and the web
#: server's request limit is 60 s: a handful of slow links must not take the whole request down. «فاضل N» says what is left.
BULK_CAP = 10
BULK_SECONDS = 25

WORDS = {
    "ar": {
        "head": "لينك الدفع", "make": "لينك دفع", "wa": "ابعته واتساب", "copy": "نسخ اللينك", "post_first": "رحّلها الأول", "no_phone": "مفيش رقم تليفون لولي الأمر",
        "link_for": "لينك بمبلغ", "expires": "بينتهي {when}", "stale": "اللينك ده مبلغه أكبر من المطلوب دلوقتي؛ الغيه واعمل واحد جديد.", "cancel": "الغي اللينك",
        "made": "اتعمل لينك الدفع؛ ابعته لولي الأمر.", "reused": "فيه لينك دفع شغال للفاتورة دي بالفعل؛ ده هو.",
        "bulk": "اعمل لينكات دفع للشهر", "bulk_hint": "لينك لكل فاتورة مترحّلة لسه عليها باقي (لحد {cap} في المرة). مبيبعتش حاجة؛ الإرسال لمسة واحدة لكل ولي أمر.",
        "ready_count": "{n} فاتورة جاهزة للينك", "left": "فاضل {n}", "left_more": "فاضل {n} فاتورة؛ دوس «{button}» تاني لباقيهم.",
        "result": "لينكات الدفع للشهر", "back": "رجوع لمصروفات الشهر", "nothing": "مفيش نتيجة لعرضها؛ ابدأ من مصروفات الشهر.",
        "made_n": "اتعمل", "skipped_n": "اتخطّى", "failed_n": "ماتعملش", "no_send": "اللينكات اتعملت بس ولسه ماتبعتتش لحد. ابعت لكل ولي أمر بلمسة من الزر جنب اسمه.",
        "sk_draft": "مسودة (رحّلها الأول)", "sk_nothing_due": "مفيش عليها باقي", "sk_has_link": "عليها لينك شغال بالفعل",
        "invoice": "الفاتورة", "payer": "ولي الأمر", "students": "الطلاب", "amount": "المبلغ", "reason": "السبب",
        "off_note": "{message}", "settings": "الخصائص", "nothing_to_make": "مفيش فواتير جاهزة للينك في الشهر ده.",
        "wa_text": "أهلاً {payer}، باقي {amount} {cur} من مصروفات {students} عن {month}. تقدر تدفعها أونلاين من هنا: {url}",
        "unknown": "حصلت مشكلة غير متوقعة في الفاتورة دي؛ جرّب تاني.", "in_days": ("بعد يوم", "بعد يومين", "بعد {n} أيام", "بعد {n} يوم"),
        "in_hours": ("بعد ساعة", "بعد ساعتين", "بعد {n} ساعات", "بعد {n} ساعة"), "soon": "بعد أقل من ساعة", "gone": "انتهى",
    },
    "en": {
        "head": "Payment link", "make": "Payment link", "wa": "Send on WhatsApp", "copy": "Copy link", "post_first": "Post it first", "no_phone": "No phone number for the parent",
        "link_for": "Link for", "expires": "Expires {when}", "stale": "This link asks for more than what is due now; cancel it and make a new one.", "cancel": "Cancel the link",
        "made": "The payment link was made; send it to the parent.", "reused": "This invoice already has a live payment link; here it is.",
        "bulk": "Make payment links for the month", "bulk_hint": "One link for each posted invoice that still has money due (up to {cap} per click). It sends nothing; sending is one tap per parent.",
        "ready_count": "{n} invoices ready for a link", "left": "{n} left", "left_more": "{n} invoices left; press “{button}” again for the rest.",
        "result": "Payment links for the month", "back": "Back to the month's fees", "nothing": "Nothing to show; start from the month's fees.",
        "made_n": "Made", "skipped_n": "Skipped", "failed_n": "Failed", "no_send": "The links are made but nobody has been sent one yet. Send each parent theirs with the button next to their name.",
        "sk_draft": "Draft (post it first)", "sk_nothing_due": "Nothing is due on it", "sk_has_link": "Already has a live link",
        "invoice": "Invoice", "payer": "Parent", "students": "Students", "amount": "Amount", "reason": "Why",
        "off_note": "{message}", "settings": "Features", "nothing_to_make": "No invoice is ready for a link this month.",
        "wa_text": "Hello {payer}, {amount} {cur} is left on the fees of {students} for {month}. You can pay online here: {url}",
        "unknown": "Something unexpected went wrong on this invoice; try again.", "in_days": ("in 1 day", "in 2 days", "in {n} days", "in {n} days"),
        "in_hours": ("in 1 hour", "in 2 hours", "in {n} hours", "in {n} hours"), "soon": "in under an hour", "gone": "expired",
    },
}


def can_make(user):
    """The right ``create_link`` itself needs, plus whatever opens the fees or the dues screen."""

    from .views import DUES_VIEW, FEES_VIEW

    return user_has_permission(user, services.RECEIVE) and (user_has_permission(user, FEES_VIEW) or user_has_permission(user, DUES_VIEW))


def fee_invoices(queryset=None):
    """The fee invoices of the entity being worked in: those with a fee charge of one of its students (what the fees screen lists)."""

    from entities import scope as entity_scope
    from sales.models import SalesInvoice

    from .models import Student
    from .services import scoped

    rows = SalesInvoice.objects.all() if queryset is None else queryset
    rows = entity_scope.scope(rows, entity_scope.SALES_INVOICE)
    return rows.filter(fee_charges__enrollment__student__in=scoped(Student.objects)).exclude(status="cancelled").distinct()


def expiry_words(expires_at, lang, now=None):
    words = WORDS[lang]
    seconds = (expires_at - (now or timezone.now())).total_seconds()
    if seconds <= 0:
        return words["gone"]
    hours = round(seconds / 3600)
    if hours < 1:
        return words["soon"]
    unit, n = ("in_days", hours // 24) if hours >= 24 else ("in_hours", hours)
    one, two, few, many = words[unit]
    if n == 1:
        return one
    if n == 2:
        return two
    return (few if 3 <= n <= 10 else many).format(n=n)


def whatsapp_url(payer, text):
    """A ``wa.me`` link to the payer's number, or '' when there is none (the copy button still works)."""

    from reports.aging import whatsapp_number

    number = whatsapp_number(payer.whatsapp or payer.phone)
    return f"https://wa.me/{number}?text={quote(text)}" if number else ""


class State:
    """What one invoice's cell shows. ``kind``: draft, paid (nothing due), problem, ready or live."""

    def __init__(self, invoice, kind, **extra):
        self.invoice, self.kind = invoice, kind
        self.ask, self.live, self.url, self.whatsapp, self.problem = extra.get("ask", ZERO), extra.get("live"), "", "", extra.get("problem", "")
        self.stale, self.expires, self.expires_at = False, "", None
        self.students, self.month, self.payer = extra.get("students", []), extra.get("month"), invoice.customer


class Board:
    """Posted fee invoices that may still have money due, for one user and language, read once."""

    def __init__(self, user, lang):
        self.user, self.lang = user, lang
        self.words = WORDS[lang]
        self.can_make = can_make(user)
        self.problem_code = ""
        if not services.enabled():
            self.problem_code = "off"
        elif services.connected() is None:
            self.problem_code = "not_connected"
        else:
            from django.conf import settings

            if not getattr(settings, "PUBLIC_BASE_URL", ""):
                self.problem_code = "no_base"
        self.available = not self.problem_code
        self.problems, self._boxes, self.states, self.by_payer, self.loaded = [], {}, {}, {}, False

    @property
    def shown(self):
        """Whether the actions are offered at all: online payment on and connected, and the user may make links."""

        return self.available and self.can_make

    def message(self):
        return services.MESSAGES[self.lang][self.problem_code] if self.problem_code else ""

    def _box_problem(self, invoice):
        entity = services.entity_of(invoice)
        key = getattr(entity, "pk", None)
        if key not in self._boxes:
            try:
                services.gateway_box(entity, self.lang)
                self._boxes[key] = ""
            except ValidationError as error:
                self._boxes[key] = error.messages[0]
                if error.messages[0] not in self.problems:
                    self.problems.append(error.messages[0])
        return self._boxes[key]

    def load(self):
        if self.loaded:
            return self
        self.loaded = True
        from parties.services import balances
        from payments.models import LinkStatus, PaymentLink
        from settlements import services as settlements

        from .models import FeeCharge

        if not self.available:
            return self
        invoices = list(fee_invoices().filter(status="posted", remaining_due__gt=0).select_related("customer", "selling_location__entity").order_by("-invoice_date", "-pk"))
        if not invoices:
            return self
        services.expire_stale()  # once; a live link is then simply a link in status «new»
        applied = settlements.applied(invoices)
        links = {link.invoice_id: link for link in PaymentLink.objects.filter(invoice__in=invoices, status=LinkStatus.NEW).select_related("invoice")}
        charges = {}
        for charge in FeeCharge.objects.filter(invoice__in=invoices).select_related("enrollment__student").order_by("pk"):
            names = charges.setdefault(charge.invoice_id, {"students": [], "month": charge.month})
            if charge.enrollment.student.name not in names["students"]:
                names["students"].append(charge.enrollment.student.name)
        payers = {invoice.customer_id: invoice.customer for invoice in invoices}
        owed = balances("customer", payers.values())
        room = {pk: max(owed[pk], ZERO) for pk in payers}  # the parent's account, spent newest invoice first (the aging is oldest first)
        for invoice in invoices:  # newest first
            outstanding = money_round(Decimal(invoice.remaining_due) - applied.get(invoice.pk, ZERO))
            ask = min(max(outstanding, ZERO), room[invoice.customer_id])
            if outstanding > 0:
                room[invoice.customer_id] -= ask
            info = charges.get(invoice.pk, {"students": [], "month": None})
            live = links.get(invoice.pk)
            if live is not None:
                state = State(invoice, "live", ask=ask, live=live, **info)
                state.stale = not (services.is_payable(live)) or live.amount > ask
            elif ask <= 0:
                continue  # nothing due: no action (a missing state reads as «paid»)
            elif self._box_problem(invoice):
                state = State(invoice, "problem", ask=ask, problem=self._box_problem(invoice), **info)
            else:
                state = State(invoice, "ready", ask=ask, **info)
            self.states[invoice.pk] = state
            self.by_payer.setdefault(invoice.customer_id, []).append(state)
        for rows in self.by_payer.values():
            rows.reverse()  # oldest first, the order the aging reads
        return self

    def state(self, invoice):
        self.load()
        if invoice.status != "posted":
            return State(invoice, "draft")
        state = self.states.get(invoice.pk)
        if state is None:
            return State(invoice, "paid")
        if state.kind == "live":
            self.dress(state)
        return state

    def payer_states(self, payer_pk):
        self.load()
        return [self.dress(state) if state.kind == "live" else state for state in self.by_payer.get(payer_pk, [])]

    def dress(self, state):
        """The live link's address, its expiry in words and the WhatsApp link (once)."""

        if state.url or state.whatsapp or state.live is None:
            return state
        from printing.company import company_details
        from workorders.public import CURRENCY_WORDS

        from . import billing

        live = state.live
        state.expires_at = timezone.localtime(live.expires_at)
        state.expires = self.words["expires"].format(when=expiry_words(live.expires_at, self.lang))
        state.url = services.link_url(live, self.lang) or ""
        if state.url and not state.stale:
            currency = company_details()["currency"]
            text = self.words["wa_text"].format(
                payer=state.payer.name, amount=f"{Decimal(live.amount):,.2f}", cur=CURRENCY_WORDS.get(currency, {}).get(self.lang, currency),
                students=("، " if self.lang == "ar" else ", ").join(state.students), month=billing.month_label(state.month, self.lang) if state.month else "", url=state.url)
            state.whatsapp = whatsapp_url(state.payer, text)
        return state

    @property
    def has_open(self):
        self.load()
        return bool(self.states)


def board_for(request, lang):
    """One ``Board`` per request, whichever tag asks first."""

    board = getattr(request, "_edu_paylink_board", None)
    if board is None:
        board = request._edu_paylink_board = Board(request.user, lang)
    return board


def make_or_show(invoice, user, lang):
    """(link, made). A live link is shown, never replaced (``create_link`` itself refuses a second one: «live»). Anything else
    is left to ``create_link``, so a draft, a fully paid invoice or a gateway that is not answering are refused with its own message."""

    words = services.MESSAGES[lang]
    board = Board(user, lang)
    if invoice.status != "posted" or not board.available:
        services.create_link(invoice, user, None, lang)  # raises the existing message: draft, switched off, no gateway, no public address
    state = board.state(invoice)
    if state.kind == "live":
        return state.live, False
    if state.kind in ("paid", "draft"):
        raise ValidationError(words["nothing_due"])
    if state.kind == "problem":
        raise ValidationError(state.problem)
    try:
        return services.create_link(invoice, user, state.ask, lang), True
    except ValidationError as error:
        live = services.live_link(invoice)
        if live is not None and error.messages[0] == words["live"]:  # someone else's click got there first
            return live, False
        raise


def bulk(first, user, lang, cap=None, seconds=None):
    """One ``create_link`` per open posted fee invoice of the month, each in its own transaction (``create_link`` is atomic), so one
    failure undoes nothing else. Returns plain data (invoice ids and texts) for the result screen."""

    from . import billing

    cap, seconds = BULK_CAP if cap is None else cap, BULK_SECONDS if seconds is None else seconds
    board = Board(user, lang)
    if not board.available:
        raise ValidationError(board.message())
    result = {"month": f"{first:%Y-%m}", "made": [], "skipped": [], "failed": [], "left": 0}
    started, tried = time.monotonic(), 0
    for row in billing.billed(first):
        invoice = row["invoice"]
        state = board.state(invoice)
        reason = {"draft": "draft", "paid": "nothing_due", "live": "has_link"}.get(state.kind)
        if reason:
            result["skipped"].append([invoice.pk, reason])
        elif state.kind == "problem":
            result["failed"].append([invoice.pk, state.problem])
        elif tried >= cap or (tried and time.monotonic() - started > seconds):
            result["left"] += 1
        else:
            tried += 1
            try:
                services.create_link(invoice, user, state.ask, lang)
            except ValidationError as error:
                result["failed"].append([invoice.pk, error.messages[0]])
            except Exception:  # one bad invoice must not stop the rest; the cause is in the log
                log.exception("E4a bulk payment link failed for invoice %s", invoice.pk)
                result["failed"].append([invoice.pk, board.words["unknown"]])
            else:
                result["made"].append(invoice.pk)
    return result
