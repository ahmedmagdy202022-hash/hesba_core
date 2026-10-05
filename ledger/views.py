"""GL-001 screens: the chart of accounts."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import services
from .models import Account

WORDS = {
    "ar": {"title": "دليل الحسابات", "intro": "شجرة الحسابات اللي القيود بتتسجل عليها. الحسابات الأساسية بتتربط بالمستندات لوحدها، وتقدر تضيف حسابات فرعية زي ما تحب.",
           "code": "الكود", "name": "الحساب", "type": "النوع", "control": "بيتسجل عليه تلقائيًا", "new": "حساب جديد", "edit": "تعديل",
           "parent": "تحت حساب", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "is_group": "مجموعة (مبيتسجلش عليها قيود مباشرة)",
           "active": "نشط", "save": "حفظ", "saved": "اتحفظ الحساب.", "back": "رجوع", "group": "مجموعة", "system": "أساسي"},
    "en": {"title": "Chart of accounts", "intro": "The account tree entries post to. Core accounts are linked to documents automatically; add sub-accounts as you like.",
           "code": "Code", "name": "Account", "type": "Type", "control": "Posted automatically", "new": "New account", "edit": "Edit",
           "parent": "Under", "name_ar": "Arabic name", "name_en": "English name", "is_group": "Group (takes no entries directly)",
           "active": "Active", "save": "Save", "saved": "Account saved.", "back": "Back", "group": "Group", "system": "Core"},
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
                                                    "page_title": WORDS[lang]["title"], "rows": rows, "types": services.TYPE_WORDS[lang],
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
