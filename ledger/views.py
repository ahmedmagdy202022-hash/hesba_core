"""GL-001/GL-002 screens: the chart of accounts, the journal, an account's
ledger, the trial balance and the reconciliation."""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import services
from . import reports
from .models import Account, JournalEntry, JournalLine
from .projector import ensure_fresh

WORDS = {
    "ar": {"title": "دليل الحسابات", "intro": "شجرة الحسابات اللي القيود بتتسجل عليها. الحسابات الأساسية بتتربط بالمستندات لوحدها، وتقدر تضيف حسابات فرعية زي ما تحب.",
           "code": "الكود", "name": "الحساب", "type": "النوع", "control": "بيتسجل عليه تلقائيًا", "new": "حساب جديد", "edit": "تعديل",
           "parent": "تحت حساب", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "is_group": "مجموعة (مبيتسجلش عليها قيود مباشرة)",
           "active": "نشط", "save": "حفظ", "saved": "اتحفظ الحساب.", "back": "رجوع", "group": "مجموعة", "system": "أساسي",
           "tab_accounts": "دليل الحسابات", "tab_journal": "اليومية العامة", "tab_trial": "ميزان المراجعة", "tab_recon": "المطابقة",
           "journal_intro": "كل مستند مترحّل بيظهر هنا كقيد مزدوج متوازن، متولّد تلقائيًا من دفاتر العملاء والموردين والخزن والمخزون.",
           "trial_intro": "رصيد كل حساب أول المدة وحركته في الفترة ورصيده آخر المدة. لو المدين مساوي الدائن يبقى الدفاتر متوازنة.",
           "recon_intro": "كل حساب رقابي قصاد التقرير اللي بيملك الرقم. أي فرق معناه مستند محتاج مراجعة.",
           "ledger_intro": "كل حركة على الحساب ده بالترتيب والرصيد المتحرك.",
           "date": "التاريخ", "reference": "المرجع", "memo": "البيان", "debit": "مدين", "credit": "دائن", "balance": "الرصيد",
           "opening": "أول المدة", "closing": "آخر المدة", "movement": "الحركة", "from": "من", "to": "إلى", "entity": "الكيان",
           "all_entities": "كل الكيانات (مجمّع)", "show": "عرض", "total": "الإجمالي", "balanced": "الميزان متوازن",
           "unbalanced": "الميزان مش متوازن — راجع القيود", "check": "البند", "ledger_figure": "في الدفاتر", "report_figure": "في التقرير",
           "difference": "الفرق", "ok": "كل الأرقام متطابقة", "not_ok": "في فروق محتاجة مراجعة", "empty": "مفيش قيود في الفترة دي.",
           "brought_forward": "رصيد منقول", "lines": "الأطراف", "more": "الأقدم",
           "checks": {"receivable": "العملاء", "payable": "الموردين", "cash": "الخزن", "inventory": "المخزون", "sales": "صافي المبيعات",
                      "cogs": "تكلفة البيع", "suspense": "الحساب المعلّق"}},
    "en": {"title": "Chart of accounts", "intro": "The account tree entries post to. Core accounts are linked to documents automatically; add sub-accounts as you like.",
           "code": "Code", "name": "Account", "type": "Type", "control": "Posted automatically", "new": "New account", "edit": "Edit",
           "parent": "Under", "name_ar": "Arabic name", "name_en": "English name", "is_group": "Group (takes no entries directly)",
           "active": "Active", "save": "Save", "saved": "Account saved.", "back": "Back", "group": "Group", "system": "Core",
           "tab_accounts": "Chart of accounts", "tab_journal": "General journal", "tab_trial": "Trial balance", "tab_recon": "Reconciliation",
           "journal_intro": "Every posted document appears here as a balanced double entry, built automatically from the customer, supplier, cash and stock ledgers.",
           "trial_intro": "Each account's opening balance, movement in the period and closing balance. Equal debits and credits mean the books balance.",
           "recon_intro": "Each control account against the report that owns the figure. Any difference names a document to review.",
           "ledger_intro": "Every movement on this account, in order, with the running balance.",
           "date": "Date", "reference": "Reference", "memo": "Description", "debit": "Debit", "credit": "Credit", "balance": "Balance",
           "opening": "Opening", "closing": "Closing", "movement": "Movement", "from": "From", "to": "To", "entity": "Entity",
           "all_entities": "All entities (consolidated)", "show": "Show", "total": "Total", "balanced": "The trial balance balances",
           "unbalanced": "The trial balance does not balance — review the entries", "check": "Item", "ledger_figure": "Ledger",
           "report_figure": "Report", "difference": "Difference", "ok": "All figures agree", "not_ok": "Differences need review",
           "empty": "No entries in this period.", "brought_forward": "Brought forward", "lines": "Lines", "more": "Older",
           "checks": {"receivable": "Customers", "payable": "Suppliers", "cash": "Cashboxes", "inventory": "Inventory", "sales": "Net sales",
                      "cogs": "Cost of sales", "suspense": "Suspense"}},
}

CONTROL_WORDS = {
    "ar": {"cash": "الخزن", "bank": "البنوك", "receivable": "فواتير البيع الآجلة والتحصيل", "inventory": "حركات المخزون", "vat_in": "ضريبة المشتريات",
           "vat_out": "ضريبة المبيعات", "payable": "فواتير الشراء الآجلة والسداد", "sales": "فواتير البيع", "sales_returns": "مرتجعات البيع",
           "cogs": "تكلفة البيع", "depreciation": "الإهلاك", "accumulated_depreciation": "الإهلاك", "fixed_assets": "الأصول الثابتة",
           "opening_equity": "الأرصدة الافتتاحية", "stock_gain": "زيادة الجرد", "stock_loss": "عجز الجرد", "instalments": "التقسيط",
           "intercompany": "التحويل بين الكيانات", "general_expense": "المصروفات اللي ملهاش حساب"},
    "en": {},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("accounting.view_ledger")
def accounts(request):
    lang = _lang(request)
    services.ensure_chart()
    controls = CONTROL_WORDS[lang]
    rows = [dict(row, control=controls.get(row["account"].control, row["account"].control)) for row in services.tree()]
    return render(request, "ledger/accounts.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang],
                                                    "page_title": WORDS[lang]["title"], "tab": "accounts", "rows": rows, "types": services.TYPE_WORDS[lang],
                                                    "can_manage": user_has_permission(request.user, "accounting.manage_accounts")})


@require_permission("accounting.manage_accounts")
def account_edit(request, pk=None):
    lang = _lang(request)
    services.ensure_chart()
    account = get_object_or_404(Account, pk=pk) if pk else None
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["parent"] = Account.objects.filter(pk=request.POST.get("parent") or 0).first()
        data["active"] = request.POST.get("active") == "on"
        try:
            services.save_account(data, request.user, account, lang)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            messages.success(request, WORDS[lang]["saved"])
            return redirect(f"{reverse('ledger:accounts')}?lang={lang}")
    groups = Account.objects.filter(is_postable=False, active=True).order_by("code")
    return render(request, "ledger/account_form.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang],
                                                        "page_title": WORDS[lang]["title"], "account": account, "groups": groups, "error": error,
                                                        "post": request.POST, "selected_parent": request.GET.get("parent", "")})


PAGE_SIZE = 100
ZERO = Decimal("0")


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _filters(request):
    from entities.models import Entity

    entities = list(Entity.objects.filter(active=True).order_by("-is_main", "code"))
    entity = next((e for e in entities if str(e.pk) == request.GET.get("entity", "")), None)
    return {"date_from": _date(request.GET.get("from")), "date_to": _date(request.GET.get("to")), "entity": entity, "entities": entities}


def _base(request, lang, tab, **extra):
    words = WORDS[lang]
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words[f"tab_{tab}"], "tab": tab}
    context.update(extra)
    return context


@require_permission("accounting.view_ledger")
def journal(request):
    lang = _lang(request)
    ensure_fresh()
    f = _filters(request)
    entries = JournalEntry.objects.order_by("-entry_date", "-pk")
    if f["date_from"]:
        entries = entries.filter(entry_date__gte=f["date_from"])
    if f["date_to"]:
        entries = entries.filter(entry_date__lte=f["date_to"])
    if f["entity"]:
        entries = entries.filter(lines__entity=f["entity"]).distinct()
    try:
        page = max(int(request.GET.get("page", "1")), 1)
    except ValueError:
        page = 1
    chunk = list(entries[(page - 1) * PAGE_SIZE: page * PAGE_SIZE + 1])
    has_more = len(chunk) > PAGE_SIZE
    chunk = chunk[:PAGE_SIZE]
    lines = JournalLine.objects.filter(entry__in=chunk).select_related("account", "entity").order_by("entry_id", "-debit", "pk")
    by_entry = {}
    for line in lines:
        by_entry.setdefault(line.entry_id, []).append(line)
    rows = [{"entry": entry, "lines": by_entry.get(entry.pk, [])} for entry in chunk]
    return render(request, "ledger/journal.html", _base(request, lang, "journal", rows=rows, page=page, has_more=has_more, **f))


@require_permission("accounting.view_ledger")
def account_ledger(request, pk):
    lang = _lang(request)
    ensure_fresh()
    account = get_object_or_404(Account, pk=pk)
    f = _filters(request)
    lines = JournalLine.objects.filter(account=account).select_related("entry", "entity", "customer", "supplier", "cashbox")
    if f["entity"]:
        lines = lines.filter(entity=f["entity"])
    opening = ZERO
    if f["date_from"]:
        before = lines.filter(entry__entry_date__lt=f["date_from"]).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = (before["d"] or ZERO) - (before["c"] or ZERO)
        lines = lines.filter(entry__entry_date__gte=f["date_from"])
    if f["date_to"]:
        lines = lines.filter(entry__entry_date__lte=f["date_to"])
    sign = 1 if account.debit_normal else -1
    running = opening
    rows = []
    for line in lines.order_by("entry__entry_date", "entry_id", "pk"):
        running += line.debit - line.credit
        rows.append({"line": line, "balance": running * sign})
    return render(request, "ledger/account_ledger.html", _base(request, lang, "trial", account=account, rows=rows, opening=opening * sign,
                                                               closing=running * sign, **f))


@require_permission("accounting.view_ledger")
def trial_balance(request):
    lang = _lang(request)
    f = _filters(request)
    data = reports.trial_balance(f["date_from"], f["date_to"], f["entity"])
    return render(request, "ledger/trial_balance.html", _base(request, lang, "trial", data=data, types=services.TYPE_WORDS[lang], **f))


@require_permission("accounting.view_ledger")
def reconciliation(request):
    lang = _lang(request)
    data = reports.reconciliation()
    checks = WORDS[lang]["checks"]
    rows = [dict(row, label=checks.get(row["key"], row["key"])) for row in data["rows"]]
    return render(request, "ledger/reconciliation.html", _base(request, lang, "recon", data=data, rows=rows))
