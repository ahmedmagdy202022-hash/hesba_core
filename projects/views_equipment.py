"""Contracting C6 (HG-129): the equipment register and a project's equipment tab (log hours, the hours list, the hire bill for
hired machines, the memo for owned ones).

Permissions, no new code:
* reading the register and the tab: the project's own (``projects.view_projects`` or ``sales.view_sales_invoices``);
* logging and deleting hours: ``projects.record_progress`` or ``sales.create_sales_invoice`` (the site engineer logs);
* the register's add and edit, and the hire bill with its withdrawal: ``purchases.create_purchase_invoice`` (the bill is a
  purchase, as the subcontract pages' is); the site engineer logs but cannot bill (403, nothing written);
* amounts (rates, the memo, profit) are for those who may read the money: profit report holders, and purchase-invoice
  holders for the hire figures. The engineer sees hours only.
A machine or a log of another entity answers 404."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_any_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label, localized_choices
from settings_core.ui_messages import translate

from master_data.models import Supplier

from . import access, costs, equipment
from .contract_views import BUY, BUY_VIEW, MANAGE, PROFIT, _context
from .models import Equipment, EquipmentKind, Project
from .views import PROJECT_SCREENS, _date, _lang, _pick

WORDS = {
    "ar": {
        "register_title": "سجل المعدات", "register_intro": "المعدات اللي الشركة بتملكها (بسعر ساعة داخلي) واللي بتستأجرها من موردين (بسعر المورد للساعة). ساعات التشغيل بتتسجل على المشروع من تاب «المعدات» فيه.",
        "name": "اسم المعدة", "kind": "النوع", "supplier": "المورد", "asset": "الأصل الثابت", "no_asset": "من غير أصل ثابت", "rate": "سعر الساعة", "status": "الحالة",
        "hours_total": "ساعات التشغيل", "active": "شغّالة", "stopped": "موقوفة", "save": "حفظ", "add": "إضافة معدة", "edit_title": "تعديل «{name}»",
        "back_register": "سجل المعدات", "rate_hint": "تغيير السعر بيمشي على الساعات الجاية بس؛ الساعات اللي اتسجلت بتفضل بسعرها.",
        "owned_hint": "ملك: السعر ده تحميل داخلي على المشروع للعلم بس، مش بيدخل الدفاتر.", "hired_hint": "إيجار: السعر ده سعر المورد، وفاتورة الإيجار بتتعمل بيه.",
        "empty": "لسه مفيش معدات.", "saved": "اتحفظت المعدة «{name}».", "view_only": "التعديل لصاحب صلاحية فاتورة الشراء بس.", "open_register": "سجل المعدات",
        "tab_title": "المعدات وساعات التشغيل", "tab_intro": "مهندس الموقع بيسجّل ساعات كل معدة. الإيجار بيتفوتر مرة واحدة، والمعدة الملك بتتحمّل على المشروع للعلم بس.",
        "log_title": "تسجيل ساعات", "equipment": "المعدة", "pick_equipment": "اختار المعدة", "date": "التاريخ", "hours": "الساعات", "operator": "المشغّل", "log_btn": "سجّل الساعات",
        "logged": "اتسجّلت {hours} ساعة على «{name}».", "logs_title": "ساعات التشغيل", "amount": "القيمة", "billed": "اتفوترت", "unbilled": "لسه", "invoice": "الفاتورة",
        "delete": "امسح", "deleted": "اتمسحت الساعات.", "no_logs": "مفيش ساعات مسجّلة على المشروع ده.", "no_machines": "مفيش معدات نشطة. سجّل معدة في سجل المعدات الأول.",
        "hire_title": "إيجار معدات لسه ما اتفوترش", "hire_hint": "فاتورة الإيجار بتتعمل مسودة (سطر لكل معدة) وبتترحّل من شاشة المشتريات؛ وساعاتها ما بتتفوترش تاني.",
        "bill_btn": "اعمل فاتورة الإيجار", "billed_ok": "اتعملت مسودة فاتورة إيجار {invoice}؛ راجعها ورحّلها من شاشة المشتريات.", "machines": "المعدات",
        "bills_title": "فواتير الإيجار", "withdraw": "اسحب المسودة", "withdrawn": "اتسحبت فاتورة الإيجار وساعاتها رجعت.", "open_invoice": "افتح الفاتورة",
        "no_bills": "مفيش فواتير إيجار.", "memo_title": "تحميل معدات ملك", "memo_hint": "ساعات المعدات الملك × سعر الساعة الداخلي. للعلم بس: مش في الدفاتر ولا في ربح الشركة، لأن إهلاك المعدة محسوب فيهم.",
        "profit_before": "ربح المشروع قبل التحميل", "profit_after": "ربح المشروع بعد التحميل", "loss": "خسارة", "total_hours": "إجمالي الساعات", "closed": "المشروع اتقفل أو اتلغى؛ التسجيل متوقف.",
        "no_owned": "مفيش ساعات معدات ملك على المشروع ده.", "entity_note": "الأرقام دي للكيان اللي شغال فيه.", "view_hours_only": "الأسعار والفواتير لصاحب صلاحية المشتريات أو تقرير الأرباح.",
        "kind_owned": "ملك", "kind_hired": "إيجار",
    },
    "en": {
        "register_title": "Equipment register", "register_intro": "The machines the company owns (at an internal hour rate) and those it hires from suppliers (at the supplier's hour rate). Operating hours are logged on a project, from its Equipment tab.",
        "name": "Machine name", "kind": "Kind", "supplier": "Supplier", "asset": "Fixed asset", "no_asset": "No fixed asset", "rate": "Hour rate", "status": "Status",
        "hours_total": "Operating hours", "active": "In use", "stopped": "Switched off", "save": "Save", "add": "Add a machine", "edit_title": "Edit “{name}”",
        "back_register": "Equipment register", "rate_hint": "A new rate applies to the hours logged from now on; hours already logged keep their rate.",
        "owned_hint": "Owned: this rate is an internal charge to the project, for information only; it never enters the books.", "hired_hint": "Hired: this is the supplier's rate, and the hire bill is made with it.",
        "empty": "No equipment yet.", "saved": "Machine “{name}” saved.", "view_only": "Only purchase-invoice holders can change the register.", "open_register": "Equipment register",
        "tab_title": "Equipment and operating hours", "tab_intro": "The site engineer logs each machine's hours. Hire is billed once, and an owned machine is charged to the project for information only.",
        "log_title": "Log hours", "equipment": "Machine", "pick_equipment": "Choose the machine", "date": "Date", "hours": "Hours", "operator": "Operator", "log_btn": "Log the hours",
        "logged": "{hours} hours logged on “{name}”.", "logs_title": "Operating hours", "amount": "Amount", "billed": "Billed", "unbilled": "Not yet", "invoice": "Invoice",
        "delete": "Delete", "deleted": "Hours deleted.", "no_logs": "No hours logged on this project.", "no_machines": "No active machines. Add one in the equipment register first.",
        "hire_title": "Hired equipment not billed yet", "hire_hint": "The hire bill is made as a draft (one line per machine) and posted from the purchases screen; its hours are never billed again.",
        "bill_btn": "Make the hire bill", "billed_ok": "Hire bill draft {invoice} made; review and post it from the purchases screen.", "machines": "Machines",
        "bills_title": "Hire bills", "withdraw": "Withdraw the draft", "withdrawn": "Hire bill withdrawn and its hours are free again.", "open_invoice": "Open the invoice",
        "no_bills": "No hire bills.", "memo_title": "Owned-equipment charge", "memo_hint": "Owned machines' hours x the internal hour rate. For information only: not in the books or the company's profit, because the machine's depreciation already is.",
        "profit_before": "Project profit before the charge", "profit_after": "Project profit after the charge", "loss": "Loss", "total_hours": "Total hours", "closed": "This project is done or cancelled; logging is stopped.",
        "no_owned": "No owned-equipment hours on this project.", "entity_note": "These figures are for the entity being worked in.", "view_hours_only": "Rates and bills are for purchases or profit-report holders.",
        "kind_owned": "Owned", "kind_hired": "Hired",
    },
}


def _need(request, *codes):
    if not any(user_has_permission(request.user, code) for code in codes):
        raise PermissionDenied("This action needs " + " or ".join(codes) + ".")


def _gate_purchases():
    from settings_core.module_gate import closed_module

    if closed_module(reverse("purchases:list")) is not None:
        raise Http404


def can_edit_register(user):
    return user_has_permission(user, BUY) or user_has_permission(user, MANAGE)


def _money_flags(user):
    return {"can_money": can_edit_register(user) or user_has_permission(user, PROFIT) or user_has_permission(user, BUY_VIEW)}


def _kind_label(kind, lang):
    return choice_label(Equipment(kind=kind), "kind", lang)


def _fields(data):
    """The typed form, read into the service's input (other entities' suppliers are not filtered: suppliers are group-wide)."""

    from fixed_assets.models import FixedAsset

    return {"name": data.get("name", ""), "kind": data.get("kind"), "hour_rate": data.get("hour_rate", ""),
            "supplier": _pick(Supplier, data.get("supplier"), active=True), "fixed_asset": _pick(FixedAsset, data.get("fixed_asset")),
            "active": data.get("active", "1") != "0"}


def _form_context(lang):
    from fixed_assets.models import FixedAsset
    from fixed_assets.services import assets_enabled

    return {"suppliers": Supplier.objects.filter(active=True).order_by("name"),
            "assets": FixedAsset.objects.filter(status="active").order_by("code") if assets_enabled() else [],
            "kinds": [(value, _kind_label(value, lang)) for value in EquipmentKind.values]}


def _base_context(request, lang, **extra):
    from .views import _base

    context = _base(request, **extra)
    context.update(ew=WORDS[lang], can_edit_register=can_edit_register(request.user), **_money_flags(request.user))
    return context


# ---- the register ----

@require_any_permission(*PROJECT_SCREENS)
def register(request):
    lang = _lang(request)
    words = WORDS[lang]
    error, form = "", {"kind": EquipmentKind.OWNED, "active": "1"}
    if request.method == "POST":
        _need(request, BUY, MANAGE)
        form = request.POST
        try:
            machine = equipment.save_equipment(request.user, _fields(request.POST), lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved"].format(name=machine.name))
            return redirect(f"{reverse('projects:equipment_register')}?lang={lang}")
    rows = []
    for machine in equipment.in_scope(Equipment.objects.select_related("supplier", "fixed_asset")):
        from django.db.models import Sum

        hours = machine.logs.aggregate(total=Sum("hours"))["total"] or 0
        rows.append({"machine": machine, "kind_label": _kind_label(machine.kind, lang), "hours": hours,
                     "url": f"{reverse('projects:equipment_edit', args=[machine.pk])}?lang={lang}"})
    context = _base_context(request, lang, page_title=words["register_title"], rows=rows, error=error, form=form, **_form_context(lang))
    return render(request, "projects/equipment_register.html", context)


@require_any_permission(*PROJECT_SCREENS)
def machine_edit(request, eid):
    lang = _lang(request)
    words = WORDS[lang]
    machine = get_object_or_404(equipment.in_scope(Equipment.objects.select_related("supplier", "fixed_asset")), pk=eid)
    error = ""
    form = {"name": machine.name, "kind": machine.kind, "hour_rate": machine.hour_rate, "supplier": str(machine.supplier_id or ""),
            "fixed_asset": str(machine.fixed_asset_id or ""), "active": "1" if machine.active else "0"}
    if request.method == "POST":
        _need(request, BUY, MANAGE)
        form = request.POST
        try:
            equipment.save_equipment(request.user, _fields(request.POST), machine, lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved"].format(name=request.POST.get("name", machine.name).strip() or machine.name))
            return redirect(f"{reverse('projects:equipment_register')}?lang={lang}")
    context = _base_context(request, lang, page_title=words["edit_title"].format(name=machine.name), machine=machine, error=error, form=form,
                            **_form_context(lang))
    return render(request, "projects/equipment_edit.html", context)


# ---- a project's equipment tab ----

def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


@require_any_permission(*PROJECT_SCREENS)
def equipment_tab(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error, posted = "", {}
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "log":
                _need(request, access.RECORD, access.MANAGE)
                posted = request.POST
                raw = str(request.POST.get("equipment") or "")
                machine = equipment.in_scope(Equipment.objects).filter(pk=raw).first() if raw.isdigit() else None
                if machine is None and raw.isdigit():
                    raise Http404  # a machine of another entity (or none at all) is not there for this user
                if machine is None:
                    raise ValidationError(equipment.MESSAGES[lang]["equipment"])
                log = equipment.log_hours(project, request.user, equipment=machine, work_date=_date(request.POST.get("work_date")),
                                          hours=request.POST.get("hours"), operator=request.POST.get("operator", ""), lang=lang)
                messages.success(request, words["logged"].format(hours=format(log.hours.normalize(), "f"), name=machine.name))
            elif action == "delete_log":
                _need(request, access.RECORD, access.MANAGE)
                raw = str(request.POST.get("log") or "")
                log = equipment.logs_in_scope(project.equipment_logs.all()).filter(pk=raw).first() if raw.isdigit() else None
                if log is None:
                    raise Http404
                equipment.delete_log(project, log, request.user, lang)
                messages.success(request, words["deleted"])
            elif action == "bill":
                _need(request, BUY)
                _gate_purchases()
                raw = str(request.POST.get("supplier") or "")
                supplier = _pick(Supplier, raw, active=True)
                if supplier is None:
                    raise Http404
                made = equipment.bill_hire(project, request.user, supplier, bill_date=_date(request.POST.get("bill_date")), lang=lang)
                messages.success(request, words["billed_ok"].format(invoice=", ".join(invoice.invoice_number for invoice in made)))
            elif action == "withdraw":
                _need(request, BUY)
                _gate_purchases()
                raw = str(request.POST.get("invoice") or "")
                link = project.purchases.filter(invoice_id=raw, heading="equipment").select_related("invoice").first() if raw.isdigit() else None
                if link is None or not costs._here(project.purchases.filter(pk=link.pk)).exists():
                    raise Http404
                equipment.withdraw_hire(project, link.invoice, request.user, lang)
                messages.success(request, words["withdrawn"])
            else:
                raise Http404
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            return redirect(f"{reverse('projects:equipment', args=[project.pk])}?lang={lang}")
    data = equipment.figures(project)
    context = _context(request, project, "equipment", ew=words, error=error, posted=posted, today=timezone.localdate().isoformat(),
                       can_log=access.can_write(request.user), **_money_flags(request.user))
    can_money = context["can_money"]
    if context["can_profit"]:
        context["memo"] = equipment.memo(project)
    else:
        context["memo"] = None
    from settings_core.module_gate import closed_module

    purchases_open = closed_module(reverse("purchases:list")) is None
    context.update(data=data, unbilled_total=sum((group["amount"] for group in data["groups"]), 0), machines=equipment.in_scope(Equipment.objects.filter(active=True)).order_by("name"), can_bill=can_money and context["can_buy"] and purchases_open,
                   owned_hours=_owned_hours(project) if not context["can_profit"] else None,
                   rows=[dict(row, kind_label=_kind_label(row["log"].equipment.kind, lang)) for row in data["logs"]])
    return render(request, "projects/equipment.html", context)


def _owned_hours(project):
    """Owned hours for a reader who sees no money: hours only, never the memo."""

    from django.db.models import Sum

    return equipment.owned_logs(project).aggregate(total=Sum("hours"))["total"] or 0
