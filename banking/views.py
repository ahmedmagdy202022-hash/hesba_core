"""T3 screens: the bank accounts, and one account's statement matched against its movements."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from cashboxes.models import CashboxMovement
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import services
from .models import StatementLine

WORDS = {
    "ar": {
        "title": "البنوك", "intro": "كل حساب بنكي هنا خزنة نوعها «حساب بنكي»: التحويلات والتحصيل والسداد بتتسجل عليه زي أي خزنة. ارفع كشف الحساب من البنك وحسبة هتطابقه لوحدها على الحركات.",
        "new": "+ حساب بنكي جديد", "account": "الحساب", "bank": "البنك", "number": "رقم الحساب", "balance": "الرصيد في الدفاتر", "last": "آخر كشف",
        "open": "سطور مش متطابقة", "none": "مفيش حسابات بنكية لسه. اعمل خزنة جديدة واختار نوعها «حساب بنكي».", "upload": "ارفع كشف حساب",
        "file": "ملف الكشف (CSV أو Excel)", "file_help": "أعمدة: التاريخ، البيان، المبلغ (أو مدين ودائن)، والرصيد لو موجود.", "closing": "رصيد آخر الكشف (اختياري)",
        "send": "ارفع وطابق", "imported": "اتقرا الكشف واتطابق {n} سطر لوحده.", "statements": "الكشوف", "auto": "طابق تاني لوحدك", "auto_done": "اتطابق {n} سطر.",
        "date": "التاريخ", "description": "البيان", "reference": "المرجع", "amount": "المبلغ", "state": "الحالة", "matched": "متطابق", "unmatched": "مش في الدفاتر",
        "pick": "اختار الحركة", "match": "طابق", "unmatch": "فك المطابقة", "fee": "سجّلها مصروفات بنكية", "saved": "اتحفظ.", "delete": "شيل الكشف ده",
        "deleted": "الكشف اتشال؛ مفيش رصيد اتغير.", "no_candidates": "مفيش حركة بنفس المبلغ؛ سجّلها من الخزن الأول.",
        "summary": "المطابقة", "book": "رصيد الدفاتر يوم {day}", "outstanding": "في الدفاتر ولسه مش في الكشف", "unrecorded": "في الكشف ولسه مش في الدفاتر",
        "expected": "المفروض الكشف يقول", "printed": "الكشف بيقول", "difference": "الفرق", "reconciled": "متطابق تمامًا",
        "outstanding_rows": "حركات في الدفاتر البنك لسه ماعرضهاش", "kind": "النوع", "no_statement": "لسه مفيش كشف للحساب ده.",
        "movements": "حركات الحساب", "no_outstanding": "كل حركات الفترة ظهرت في الكشف.",
    },
    "en": {
        "title": "Banks", "intro": "Each bank account here is a cashbox of kind “Bank account”: transfers, collections and payments are recorded on it like any cashbox. Upload the bank's statement and Hesba matches it against the movements by itself.",
        "new": "+ New bank account", "account": "Account", "bank": "Bank", "number": "Account number", "balance": "Balance in the books", "last": "Last statement",
        "open": "Unmatched lines", "none": "No bank accounts yet. Make a new cashbox and choose the kind “Bank account”.", "upload": "Upload a statement",
        "file": "Statement file (CSV or Excel)", "file_help": "Columns: date, description, amount (or debit and credit), and the balance if there is one.",
        "closing": "Closing balance on the statement (optional)", "send": "Upload and match", "imported": "Statement read; {n} lines matched by themselves.",
        "statements": "Statements", "auto": "Match again by itself", "auto_done": "{n} lines matched.",
        "date": "Date", "description": "Description", "reference": "Reference", "amount": "Amount", "state": "State", "matched": "Matched", "unmatched": "Not in the books",
        "pick": "Pick the movement", "match": "Match", "unmatch": "Unmatch", "fee": "Record as bank charges", "saved": "Saved.", "delete": "Remove this statement",
        "deleted": "Statement removed; no balance changed.", "no_candidates": "No movement has this amount; record it from the cashboxes first.",
        "summary": "Reconciliation", "book": "Books balance on {day}", "outstanding": "In the books, not on the statement yet", "unrecorded": "On the statement, not in the books yet",
        "expected": "The statement should say", "printed": "The statement says", "difference": "Difference", "reconciled": "Fully reconciled",
        "outstanding_rows": "Movements in the books the bank has not shown yet", "kind": "Kind", "no_statement": "No statement for this account yet.",
        "movements": "Account movements", "no_outstanding": "Every movement of the period is on the statement.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return " ".join(translate(text, lang) for text in getattr(exc, "messages", [str(exc)]))


def _context(request, lang, **extra):
    words = WORDS[lang]
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"],
               "can_work": services.can_work(request.user), "can_add": user_has_permission(request.user, "cashboxes.manage_cashboxes")}
    context.update(extra)
    return context


@require_permission("cashboxes.view_finance")
def bank_list(request):
    lang = _lang(request)
    if not services.can_view(request.user):
        raise PermissionDenied
    return render(request, "banking/list.html", _context(request, lang, rows=services.overview()))


@require_permission("cashboxes.view_finance")
def bank_account(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    if not services.can_view(request.user):
        raise PermissionDenied
    cashbox = get_object_or_404(services.accounts(), pk=pk)
    statements = list(services.statements(cashbox)[:24])  # the navigation shows the latest; any of the account's opens by its id
    chosen = request.POST.get("statement") or request.GET.get("statement")
    if chosen:
        statement = get_object_or_404(services.statements(cashbox), pk=chosen if str(chosen).isdigit() else 0)
        if statement not in statements:
            statements.append(statement)
    else:
        statement = statements[0] if statements else None
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "import":
                statement, matched = services.import_statement(cashbox, request.FILES.get("file"), request.user, lang, request.POST.get("closing_balance"))
                flash.success(request, words["imported"].format(n=matched))
            elif statement is None:
                raise Http404("No statement.")
            elif action == "auto":
                flash.success(request, words["auto_done"].format(n=services.auto_match(statement, request.user)))
            elif action == "delete":
                services.delete_statement(statement, request.user, lang)
                flash.success(request, words["deleted"])
                return redirect(f"{reverse('banking:account', args=[cashbox.pk])}?lang={lang}")
            elif action in ("match", "unmatch", "fee"):
                line = get_object_or_404(StatementLine, pk=request.POST.get("line") or 0, statement=statement)
                if action == "match":
                    movement = get_object_or_404(CashboxMovement, pk=request.POST.get("movement") or 0, cashbox=cashbox)
                    services.match(line, movement, request.user, lang)
                elif action == "unmatch":
                    services.unmatch(line, request.user)
                else:
                    services.record_fee(line, request.user, lang)
                flash.success(request, words["saved"])
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return redirect(f"{reverse('banking:account', args=[cashbox.pk])}?lang={lang}&statement={statement.pk}")
    report = services.reconciliation(statement) if statement else None
    if report:
        for line in report["lines"]:
            line.choices = [] if line.movement_id else services.candidates(line, days=None)[:100]  # same amount, any date
    book_label = words["book"].format(day=report["last"].isoformat()) if report else ""
    return render(request, "banking/account.html", _context(request, lang, cashbox=cashbox, statements=statements, statement=statement,
                                                            report=report, error=error, balance=services.balance(cashbox), book_label=book_label))
