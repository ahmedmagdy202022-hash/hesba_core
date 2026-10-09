"""STAFF-001 screens: the team list (with a quick add) and one employee's page."""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission

from .models import Employee
from .services import save_employee


VIEW, MANAGE = "master_data.view_master_data", "master_data.manage_parties"
# AUDIT-2: the staff list is for the owner, the manager and the accountant.
from reports.navigation import STAFF_AUDIENCE  # noqa: E402
WORDS = {
    "ar": {
        "page_title": "الموظفون", "title": "الموظفون والفنيون", "intro": "الفنيين والدكاترة والمصففين والمدرسين اللي بيشتغلوا مع العملاء. تقدر تربط الموظف بالمواعيد وتشوف أداءه وعمولته.",
        "new": "موظف جديد", "name": "الاسم", "title_field": "الوظيفة", "title_hint": "مثلاً: فني صيانة، دكتور، مصفف", "phone": "الموبايل",
        "commission": "نسبة العمولة %", "commission_hint": "من المبيعات المرحّلة للشغل اللي خلّصه (للتقرير بس؛ الصرف بيتسجل كمصروف).",
        "user": "حساب الدخول (اختياري)", "none": "— من غير حساب —", "active": "نشط", "notes": "ملاحظات", "save": "حفظ", "code": "الكود",
        "status": "الحالة", "on": "نشط", "off": "موقوف", "empty": "لسه مفيش موظفين.", "saved": "اتحفظ الموظف {name}.", "back": "الموظفون", "view_only": "إضافة الموظفين وتعديلهم لصاحب الصلاحية بس.",
    },
    "en": {
        "page_title": "Employees", "title": "Employees & technicians", "intro": "The technicians, doctors, stylists and teachers who serve customers. Link them to appointments and see their work and commission.",
        "new": "New employee", "name": "Name", "title_field": "Job title", "title_hint": "e.g. technician, doctor, stylist", "phone": "Mobile",
        "commission": "Commission %", "commission_hint": "Of the posted sales of work they completed (report only; paying it is recorded as an expense).",
        "user": "Login account (optional)", "none": "— no login —", "active": "Active", "notes": "Notes", "save": "Save", "code": "Code",
        "status": "Status", "on": "Active", "off": "Disabled", "empty": "No employees yet.", "saved": "Employee {name} saved.", "back": "Employees", "view_only": "Only users with the permission can add or change employees.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"],
               "can_manage": user_has_permission(request.user, MANAGE),
               "users": get_user_model().objects.filter(is_active=True, is_superuser=False).order_by("username")}
    context.update(extra)
    return context


def _post_data(request):
    return {key: request.POST.get(key, "") for key in ("name", "title", "phone", "commission_percent", "user", "notes")} | {"active": request.POST.get("active") == "1"}


@require_any_permission(*STAFF_AUDIENCE)
def employee_list(request):
    lang = _lang(request)
    form, error = {"active": True}, ""
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied("Adding employees needs master_data.manage_parties.")
        form = _post_data(request)
        try:
            employee = save_employee(form, request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["saved"].format(name=employee.name))
            return redirect(f"{reverse('staff:list')}?lang={lang}")
    return render(request, "staff/list.html", _context(request, employees=Employee.objects.select_related("user"), form=form, error=error))


@require_any_permission(*STAFF_AUDIENCE)
def employee_detail(request, pk):
    lang = _lang(request)
    employee = get_object_or_404(Employee, pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied("Changing employees needs master_data.manage_parties.")
        try:
            save_employee(_post_data(request), request.user, employee=employee, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["saved"].format(name=employee.name))
            return redirect(f"{reverse('staff:detail', args=[employee.pk])}?lang={lang}")
    form = {"name": employee.name, "title": employee.title, "phone": employee.phone, "commission_percent": employee.commission_percent,
            "user": str(employee.user_id or ""), "active": employee.active, "notes": employee.notes}
    return render(request, "staff/detail.html", _context(request, employee=employee, form=form, error=error))
