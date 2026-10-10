"""GL-001: build and maintain the chart of accounts."""

from django.core.exceptions import ValidationError
from django.db import transaction

from audit.models import AuditEventType, AuditLog

from . import chart
from .models import Account, AccountType, ExpenseAccount

MESSAGES = {
    "ar": {"code": "الكود لازم يكون أرقام، ويبدأ بكود الحساب الأب.", "taken": "الكود ده مستخدم.", "name": "اكتب اسم الحساب.",
           "parent": "اختار حساب أب (مجموعة).", "system": "ده حساب أساسي، تقدر تغيّر اسمه بس.", "used": "الحساب عليه قيود، مينفعش يتحوّل لمجموعة."},
    "en": {"code": "The code must be digits starting with the parent's code.", "taken": "This code is already used.", "name": "Enter the account name.",
           "parent": "Choose a parent group.", "system": "This is a core account; only its name can change.", "used": "The account has entries; it cannot become a group."},
}


def _activity():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return (profile.activity_slug or "") if profile is not None else ""


@transaction.atomic
def ensure_chart(activity=None):
    """Create whatever default accounts are missing; never changes existing ones."""

    from settings_core.setup_services import enabled_modules

    activity = _activity() if activity is None else activity
    existing = {account.code: account for account in Account.objects.all()}
    for code, ar, en, kind, control, postable, contra in chart.rows_for(activity, projects="projects" in enabled_modules()):
        if code in existing:
            continue
        if control and Account.objects.filter(control=control).exists():
            control = ""
        parent = existing.get(chart.parent_code(code) or "")
        existing[code] = Account.objects.create(code=code, name_ar=ar, name_en=en, account_type=kind, parent=parent, control=control,
                                                is_postable=postable, is_contra=contra, is_system=bool(control) or not postable)
    from expenses.models import ExpenseCategory

    fallback = Account.objects.filter(control="general_expense").first()
    for category in ExpenseCategory.objects.filter(ledger_link__isnull=True):
        account = existing.get(chart.EXPENSE_ACCOUNTS.get(category.code, "")) or fallback
        if account is not None:
            ExpenseAccount.objects.create(category=category, account=account)
    return Account.objects.count()


def account_for(control):
    """The account a control key posts to (GL-002)."""

    account = Account.objects.filter(control=control, active=True).first()
    if account is None:
        ensure_chart()
        account = Account.objects.filter(control=control).first()
    return account


def tree():
    """Accounts in code order with their depth, for the chart screen."""

    return [{"account": account, "depth": len(account.code) - 1 if len(account.code) <= 2 else len(account.code) // 2}
            for account in Account.objects.select_related("parent").order_by("code")]


@transaction.atomic
def save_account(data, user, account=None, lang="ar"):
    words = MESSAGES["en" if lang == "en" else "ar"]
    name = (data.get("name_ar") or "").strip()[:255]
    if not name:
        raise ValidationError(words["name"])
    name_en = (data.get("name_en") or "").strip()[:255]
    if account is not None and account.is_system:
        before = {"name_ar": account.name_ar, "name_en": account.name_en}
        account.name_ar, account.name_en = name, name_en
        account.save(update_fields=["name_ar", "name_en"])
        _audit(account, user, "rename_account", before)
        return account
    parent = data.get("parent")
    if parent is None or parent.is_postable:
        raise ValidationError(words["parent"])
    code = (data.get("code") or "").strip()
    if not code.isdigit() or not code.startswith(parent.code) or len(code) <= len(parent.code):
        raise ValidationError(words["code"])
    if Account.objects.filter(code=code).exclude(pk=getattr(account, "pk", None)).exists():
        raise ValidationError(words["taken"])
    is_group = data.get("is_group") in ("1", "on", True)
    created = account is None
    before = {} if created else {"code": account.code, "name_ar": account.name_ar, "active": account.active}
    account = account or Account(account_type=parent.account_type)
    account.code, account.name_ar, account.name_en, account.parent = code, name, name_en, parent
    account.account_type = parent.account_type
    account.is_postable = not is_group
    account.active = data.get("active", True) not in (False, "0", "", None)
    account.save()
    _audit(account, user, "create_account" if created else "update_account", before)
    return account


def _audit(account, user, action, before):
    AuditLog.objects.create(event_type=AuditEventType.CREATE if action == "create_account" else AuditEventType.UPDATE, actor=user, module="ledger",
                            action=action, object_type="ledger.Account", object_id=str(account.pk), before_data=before,
                            after_data={"code": account.code, "name_ar": account.name_ar, "name_en": account.name_en, "active": account.active})


TYPE_WORDS = {
    "ar": {AccountType.ASSET: "أصول", AccountType.LIABILITY: "خصوم", AccountType.EQUITY: "حقوق ملكية", AccountType.INCOME: "إيرادات", AccountType.EXPENSE: "مصروفات"},
    "en": {AccountType.ASSET: "Asset", AccountType.LIABILITY: "Liability", AccountType.EQUITY: "Equity", AccountType.INCOME: "Income", AccountType.EXPENSE: "Expense"},
}
