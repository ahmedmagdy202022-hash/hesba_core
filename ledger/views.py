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
from . import reports, statements
from .models import Account, JournalEntry, JournalLine
from .projector import ensure_fresh

WORDS = {
    "ar": {"title": "دليل الحسابات", "intro": "شجرة الحسابات اللي القيود بتتسجل عليها. الحسابات الأساسية بتتربط بالمستندات لوحدها، وتقدر تضيف حسابات فرعية زي ما تحب.",
           "code": "الكود", "name": "الحساب", "type": "النوع", "control": "بيتسجل عليه تلقائيًا", "new": "حساب جديد", "edit": "تعديل",
           "parent": "تحت حساب", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "is_group": "مجموعة (مبيتسجلش عليها قيود مباشرة)",
           "active": "نشط", "save": "حفظ", "saved": "اتحفظ الحساب.", "back": "رجوع", "group": "مجموعة", "system": "أساسي",
           "tab_accounts": "دليل الحسابات", "tab_journal": "اليومية العامة", "tab_income": "الأرباح والخسائر", "tab_balance": "المركز المالي (الميزانية)", "tab_cash": "حركة الفلوس (التدفقات)",
           "income_intro": "الإيرادات ناقص تكلفة البيع والمصروفات، مع مقارنة بالفترة اللي قبلها وبنفس الفترة السنة اللي فاتت.",
           "balance_intro": "اللي تملكه المنشأة (الأصول) قصاد اللي عليها (الخصوم) وحق أصحابها (حقوق الملكية) في تاريخ معيّن.",
           "cash_intro": "منين جت الفلوس وراحت فين: من النشاط، ومن الاستثمار في الأصول، ومن التمويل. والمحصّلة لازم تساوي التغيّر في الخزن والبنوك.",
           "as_of": "في تاريخ", "current": "الفترة", "previous": "الفترة اللي قبلها", "last_year": "نفس الفترة السنة اللي فاتت",
           "export": "تنزيل Excel (CSV)", "print": "طباعة / PDF", "group_note": "القوائم المجمّعة بتلغي الحسابات الجارية بين الكيانات تلقائيًا.",
           "sections": {"revenue": "الإيرادات", "cost_of_sales": "تكلفة المبيعات", "operating_expenses": "المصروفات التشغيلية", "other_income": "إيرادات أخرى",
                        "current_assets": "الأصول المتداولة", "fixed_assets": "الأصول الثابتة", "current_liabilities": "الخصوم المتداولة",
                        "long_term_liabilities": "الخصوم طويلة الأجل", "equity": "حقوق الملكية"},
           "gross_profit": "مجمل الربح", "operating_profit": "ربح التشغيل", "net_profit": "صافي الربح", "net_loss": "صافي الخسارة",
           "unclosed_earnings": "أرباح الفترة (لسه متقفلتش)", "total_assets": "إجمالي الأصول", "total_liabilities": "إجمالي الخصوم",
           "total_le": "إجمالي الخصوم وحقوق الملكية", "sheet_balanced": "الميزانية متوازنة", "sheet_unbalanced": "الميزانية مش متوازنة — راجع المطابقة",
           "operating": "التدفقات من النشاط", "investing": "التدفقات من الاستثمار", "financing": "التدفقات من التمويل",
           "add_depreciation": "يُضاف: الإهلاك (مصروف من غير فلوس)", "less_disposal": "يُخصم: أرباح بيع أصول (فلوسها في الاستثمار)",
           "disposal_proceeds": "أرباح بيع أصول", "net_change": "صافي التغيّر في النقدية", "cash_opening": "النقدية أول المدة",
           "cash_closing": "النقدية آخر المدة", "flow_ok": "متطابقة مع رصيد الخزن والبنوك", "flow_bad": "مش متطابقة — راجع المطابقة",
           "change_in": "التغيّر في", "tab_trial": "ميزان المراجعة", "tab_recon": "المطابقة",
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
           "tab_accounts": "Chart of accounts", "tab_journal": "General journal", "tab_income": "Profit & loss", "tab_balance": "Balance sheet", "tab_cash": "Cash flow",
           "income_intro": "Revenue less cost of sales and expenses, compared with the previous period and the same period last year.",
           "balance_intro": "What the business owns (assets) against what it owes (liabilities) and its owners' share (equity) on a date.",
           "cash_intro": "Where cash came from and went: operations, investment in assets, and financing. The total equals the change in cash and bank.",
           "as_of": "As of", "current": "Period", "previous": "Previous period", "last_year": "Same period last year",
           "export": "Download Excel (CSV)", "print": "Print / PDF", "group_note": "Group statements cancel the balances due between entities automatically.",
           "sections": {"revenue": "Revenue", "cost_of_sales": "Cost of sales", "operating_expenses": "Operating expenses", "other_income": "Other income",
                        "current_assets": "Current assets", "fixed_assets": "Fixed assets", "current_liabilities": "Current liabilities",
                        "long_term_liabilities": "Long-term liabilities", "equity": "Equity"},
           "gross_profit": "Gross profit", "operating_profit": "Operating profit", "net_profit": "Net profit", "net_loss": "Net loss",
           "unclosed_earnings": "Profit for the period (not yet closed)", "total_assets": "Total assets", "total_liabilities": "Total liabilities",
           "total_le": "Total liabilities and equity", "sheet_balanced": "The balance sheet balances", "sheet_unbalanced": "The balance sheet does not balance — check the reconciliation",
           "operating": "Operating activities", "investing": "Investing activities", "financing": "Financing activities",
           "add_depreciation": "Add: depreciation (a non-cash expense)", "less_disposal": "Less: gain on asset sales (its cash is under investing)",
           "disposal_proceeds": "Gain on asset sales", "net_change": "Net change in cash", "cash_opening": "Cash at the start",
           "cash_closing": "Cash at the end", "flow_ok": "Agrees with the cashbox and bank balances", "flow_bad": "Does not agree — check the reconciliation",
           "change_in": "Change in", "tab_trial": "Trial balance", "tab_recon": "Reconciliation",
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
                                                    "page_title": WORDS[lang]["title"], "tab": "accounts", **_tabs(lang), "rows": rows, "types": services.TYPE_WORDS[lang],
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

    from entities.current import allowed_entities, current_entity

    every = list(Entity.objects.filter(active=True).order_by("-is_main", "code"))
    entities = allowed_entities(request.user)
    restricted = len(entities) < len(every)
    if "entity" in request.GET:
        entity = next((e for e in entities if str(e.pk) == request.GET.get("entity", "")), None)
    else:
        # ENT-002: the books open on the entity being worked in.
        entity = current_entity()
    if entity is None and restricted:
        # HG-034: someone who belongs to some entities only never sees the
        # consolidated books, and cannot ask for another entity's.
        entity = current_entity() or entities[0]
    return {"date_from": _date(request.GET.get("from")), "date_to": _date(request.GET.get("to")), "entity": entity, "entities": entities, "group_wide": not restricted}


# R2: what an owner reads first (did I make money, what do I own and owe,
# where did the cash go), then the accountant's tools.
LEDGER_TABS = (("income", "ledger:income_statement"), ("balance", "ledger:balance_sheet"), ("cash", "ledger:cash_flow"),
               ("accounts", "ledger:accounts"), ("journal", "ledger:journal"), ("trial", "ledger:trial_balance"),
               ("recon", "ledger:reconciliation"))


def _tabs(lang):
    return {"ledger_tabs": LEDGER_TABS, "tab_labels": [(key, WORDS[lang][f"tab_{key}"]) for key, _ in LEDGER_TABS]}


def _base(request, lang, tab, **extra):
    words = WORDS[lang]
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words[f"tab_{tab}"], "tab": tab, **_tabs(lang)}
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


def _name(account, lang):
    return account.name_en if lang == "en" and account.name_en else account.name_ar


def _csv(filename, rows):
    import csv
    import io

    from django.http import HttpResponse

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _window(f):
    if not f["date_from"] and not f["date_to"]:
        f["date_from"], f["date_to"] = statements.default_window()
    return f


INCOME_ORDER = (("section", "revenue"), ("section", "cost_of_sales"), ("total", "gross_profit"), ("section", "operating_expenses"),
                ("total", "operating_profit"), ("section", "other_income"), ("total", "net_profit"))


@require_permission("accounting.view_ledger")
def income_statement(request):
    lang = _lang(request)
    words = WORDS[lang]
    f = _window(_filters(request))
    data = statements.income_statement(f["date_from"], f["date_to"], f["entity"])
    columns = [("current", data)] + [(name, data["comparisons"][name]) for name in ("previous", "last_year") if name in data["comparisons"]]
    blocks = []
    for kind, key in INCOME_ORDER:
        if kind == "total":
            blocks.append({"kind": "total", "key": key, "label": words[key], "values": [c[key] for _, c in columns]})
            continue
        accounts = {}
        for _, column in columns:
            for line in column["sections"][key]["lines"]:
                accounts.setdefault(line["account"].pk, line["account"])
        lines = []
        for account in sorted(accounts.values(), key=lambda a: a.code):
            values = [next((l["amount"] for l in c["sections"][key]["lines"] if l["account"].pk == account.pk), ZERO) for _, c in columns]
            lines.append({"account": account, "name": _name(account, lang), "values": values})
        blocks.append({"kind": "section", "key": key, "label": words["sections"][key], "lines": lines,
                       "values": [c["sections"][key]["total"] for _, c in columns]})
    headers = [words[name] for name, _ in columns]
    if request.GET.get("format") == "csv":
        rows = [[words["tab_income"], f"{f['date_from']} → {f['date_to']}"], ["", *headers]]
        for block in blocks:
            if block["kind"] == "section":
                rows.append([block["label"]])
                rows += [[f"{line['account'].code} {line['name']}", *line["values"]] for line in block["lines"]]
            rows.append([block["label"], *block["values"]])
        return _csv(f"income-statement-{f['date_from']}-{f['date_to']}.csv", rows)
    return render(request, "ledger/income_statement.html", _base(request, lang, "income", blocks=blocks, headers=headers,
                                                                 windows=[c["window"] for _, c in columns], data=data, **f))


@require_permission("accounting.view_ledger")
def balance_sheet(request):
    lang = _lang(request)
    words = WORDS[lang]
    f = _filters(request)
    as_of = f["date_to"] or statements.default_window()[1]
    data = statements.balance_sheet(as_of, f["entity"])
    sides = []
    for side, keys, total_key, total_label in (("assets", ("current_assets", "fixed_assets"), "total_assets", words["total_assets"]),
                                               ("le", ("current_liabilities", "long_term_liabilities", "equity"), "total_liabilities_and_equity", words["total_le"])):
        blocks = []
        for key in keys:
            section = data["sections"][key]
            lines = [{"account": l["account"], "name": _name(l["account"], lang), "amount": l["amount"]} for l in section["lines"]]
            if key == "equity" and data["unclosed_earnings"]:
                lines.append({"account": None, "name": words["unclosed_earnings"], "amount": data["unclosed_earnings"]})
            blocks.append({"label": words["sections"][key], "lines": lines, "total": section["total"]})
        sides.append({"key": side, "blocks": blocks, "total_label": total_label, "total": data[total_key]})
    if request.GET.get("format") == "csv":
        rows = [[words["tab_balance"], f"{words['as_of']} {as_of}"]]
        for side in sides:
            for block in side["blocks"]:
                rows.append([block["label"]])
                rows += [[(f"{l['account'].code} " if l["account"] else "") + l["name"], l["amount"]] for l in block["lines"]]
                rows.append([block["label"], block["total"]])
            rows.append([side["total_label"], side["total"]])
        return _csv(f"balance-sheet-{as_of}.csv", rows)
    return render(request, "ledger/balance_sheet.html", _base(request, lang, "balance", data=data, sides=sides, as_of=as_of, **f))


@require_permission("accounting.view_ledger")
def cash_flow(request):
    lang = _lang(request)
    words = WORDS[lang]
    f = _window(_filters(request))
    data = statements.cash_flow(f["date_from"], f["date_to"], f["entity"])

    def named(lines):
        return [{"account": l["account"], "name": f"{words['change_in']} {_name(l['account'], lang)}", "amount": l["amount"]} for l in lines]

    operating = [{"account": None, "name": words["net_profit"], "amount": data["net_profit"]}]
    if data["depreciation"]:
        operating.append({"account": None, "name": words["add_depreciation"], "amount": data["depreciation"]})
    if data["disposal_gain"]:
        operating.append({"account": None, "name": words["less_disposal"], "amount": -data["disposal_gain"]})
    investing = ([{"account": None, "name": words["disposal_proceeds"], "amount": data["disposal_gain"]}] if data["disposal_gain"] else []) + named(data["investing"])
    blocks = [{"label": words["operating"], "lines": operating + named(data["operating"]), "total": data["operating_total"]},
              {"label": words["investing"], "lines": investing, "total": data["investing_total"]},
              {"label": words["financing"], "lines": named(data["financing"]), "total": data["financing_total"]}]
    if request.GET.get("format") == "csv":
        rows = [[words["tab_cash"], f"{f['date_from']} → {f['date_to']}"]]
        for block in blocks:
            rows.append([block["label"]])
            rows += [[l["name"], l["amount"]] for l in block["lines"]]
            rows.append([block["label"], block["total"]])
        rows += [[words["net_change"], data["net_change"]], [words["cash_opening"], data["cash_opening"]], [words["cash_closing"], data["cash_closing"]]]
        return _csv(f"cash-flow-{f['date_from']}-{f['date_to']}.csv", rows)
    return render(request, "ledger/cash_flow.html", _base(request, lang, "cash", data=data, blocks=blocks, **f))
