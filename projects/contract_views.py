"""CONTRACT-002 screens: bill of quantities, certificates, payments and retention,
subcontractors, budget. Each project page shares the tabs and the contract figures."""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Supplier
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from purchases.models import PurchaseInvoice
from sales.models import CustomerPayment
from settings_core.display_labels import choice_label, localized_choices
from settings_core.ui_messages import translate

from . import contract, costs
from .models import (
    BudgetLine, Certificate, CostHeading, Project, ProjectPayment, ProjectPurchase, RetentionRelease, Subcontract, SubcontractBill, SubcontractRelease,
)
from .views import MANAGE, PROFIT, VIEW, _base, _date, _lang, _pick
from entities import scope as entity_scope

COLLECT, BUY, BUY_VIEW = "sales.receive_customer_payment", "purchases.create_purchase_invoice", "purchases.view_purchase_invoices"

WORDS = {
    "ar": {
        "tab_overview": "نظرة عامة", "tab_boq": "المقايسة", "tab_certificates": "المستخلصات", "tab_payments": "الدفعات وضمان الأعمال",
        "tab_subcontracts": "مقاولو الباطن", "tab_budget": "الموازنة والتكلفة",
        "contract": "قيمة العقد", "certified": "اتعمل بيه مستخلصات (مرحّلة)", "due_now": "مستحق الآن على صاحب المشروع", "retention_held": "ضمان أعمال محتجز",
        "advance_left": "دفعة مقدمة لسه ما اتخصمتش", "collections": "اتحصّل على المستخلصات",
        "boq_intro": "بنود المقايسة بالكمية والفئة. إجمالي المقايسة هو قيمة العقد، والمستخلصات بتتعمل بالكميات المنفذة حتى تاريخه.",
        "code": "البند", "description": "الوصف", "unit": "الوحدة", "qty": "الكمية", "rate": "الفئة", "amount": "القيمة", "certified_qty": "اتعمل بيه مستخلصات",
        "done_pct": "نسبة التنفيذ", "add_line": "+ إضافة بند", "save": "حفظ", "delete": "مسح", "total": "الإجمالي", "none": "مفيش.",
        "terms": "شروط العقد", "retention_rate": "نسبة ضمان الأعمال %", "recovery_rate": "نسبة استرداد الدفعة المقدمة من كل مستخلص %",
        "terms_hint": "ضمان الأعمال بيتحجز من كل مستخلص لحد الاستلام، والدفعة المقدمة بتتخصم من كل مستخلص بالنسبة دي لحد ما تخلص.",
        "saved": "اتحفظ.", "deleted": "اتمسح.",
        "certificates_intro": "كل مستخلص بيعمل فاتورة بيع مسودة بقيمة الأعمال؛ رحّلها من شاشة الفاتورة. ضمان الأعمال والدفعة المقدمة بيتخصموا من المستحق.",
        "number": "رقم", "date": "التاريخ", "gross": "قيمة الأعمال", "tax": "الضريبة", "invoice_total": "إجمالي الفاتورة", "retention": "ضمان أعمال",
        "recovery": "استرداد دفعة مقدمة", "net": "صافي المستحق", "status": "الحالة", "invoice": "الفاتورة",
        "new_certificate": "مستخلص جديد", "cumulative": "الكمية حتى تاريخه", "previous": "السابق", "current": "الحالي", "contract_qty": "كمية العقد",
        "lump_amount": "قيمة الأعمال المنفذة", "lump_description": "وصف المستخلص (مثلاً: أعمال الشهر الأول)", "make_certificate": "اعمل المستخلص",
        "certificate_made": "اتعمل المستخلص رقم {number} كمسودة ({invoice}). راجعه ورحّل الفاتورة.", "draft_open": "فيه مستخلص مسودة لازم يترحّل أو يتسحب الأول.",
        "certificate": "مستخلص رقم {number}", "open_invoice": "افتح الفاتورة", "print": "طباعة المستخلص", "withdraw": "اسحب المسودة",
        "withdrawn": "اتسحب المستخلص ومسودته.", "previous_value": "قيمة سابقة", "current_value": "قيمة حالية", "cumulative_value": "القيمة حتى تاريخه",
        "over_qty": "الكمية حتى تاريخه أكبر من كمية العقد", "posted_note": "اترحّلت الفاتورة: التعديل يبقى بمرتجع أو إلغاء من شاشة الفاتورة.",
        "payments_intro": "التحصيلات من صاحب المشروع بتتسجل كتحصيل عادي على حسابه وبتتعلّم للمشروع ده. الدفعة المقدمة بتتخصم من المستخلصات.",
        "receive": "استلام من صاحب المشروع", "kind": "النوع", "cashbox": "الخزنة", "receive_btn": "سجّل الاستلام", "received": "اتسجل {number}.",
        "link_payment": "اربط تحصيل متسجل", "link": "اربط", "unlink": "فك الربط", "linked": "اتربط.", "unlinked": "اتفك الربط.",
        "retention_title": "ضمان الأعمال", "release": "إفراج عن ضمان الأعمال", "release_btn": "سجّل الإفراج", "released": "اتسجل الإفراج.", "reverse_release": "إلغاء الإفراج", "release_reversed": "اتلغى الإفراج.", "reversed_on": "اتلغى في",
        "release_hint": "عند الاستلام: الإفراج بينقل المبلغ من المحتجز لمستحق على صاحب المشروع، والتحصيل نفسه بيتسجل بعد كده عادي.",
        "notes": "ملاحظات", "held_total": "المحتجز", "released_total": "اتفرج عنه",
        "subcontracts_intro": "الأعمال المسندة لمقاولي الباطن (من الموردين). كل مستخلص مقاول باطن بيعمل فاتورة شراء مسودة، وبنحجز منه ضمان الأعمال.",
        "supplier": "مقاول الباطن", "scope": "الأعمال المسندة", "value": "قيمة العقد", "new_subcontract": "إسناد أعمال لمقاول باطن", "billed": "اتعمل بيه مستخلصات",
        "remaining": "الباقي", "bill": "مستخلص لمقاول الباطن", "bill_btn": "اعمل المستخلص", "billed_ok": "اتعملت مسودة {invoice}؛ رحّلها من شاشة المشتريات.",
        "other_purchases": "مشتريات خدمات تانية للمشروع", "purchases_hint": "فواتير خدمات (إيجار معدات، نقل، خدمات موقع). الخامات المخزنية بتتصرف من المخزن.",
        "link_purchase": "اربط فاتورة شراء خدمات", "vendor": "المورد", "heading": "البند", "drafts": "مسودات",
        "budget_intro": "الموازنة المخططة لكل بند تكلفة قصاد التكلفة الفعلية من الخامات المصروفة والمصروفات والمشتريات المرتبطة.",
        "budget": "الموازنة", "actual": "الفعلي", "variance": "الفرق", "used": "المستخدم", "save_budget": "حفظ الموازنة",
        "view_only": "العرض بس: التعديل لصاحب الصلاحية.",
    },
    "en": {
        "tab_overview": "Overview", "tab_boq": "Bill of quantities", "tab_certificates": "Certificates", "tab_payments": "Payments & retention",
        "tab_subcontracts": "Subcontractors", "tab_budget": "Budget & cost",
        "contract": "Contract value", "certified": "Certified (posted)", "due_now": "Due now from the owner", "retention_held": "Retention held",
        "advance_left": "Advance not yet recovered", "collections": "Collected on certificates",
        "boq_intro": "The bill of quantities, by quantity and rate. Its total is the contract value; certificates bill the quantities done to date.",
        "code": "Item", "description": "Description", "unit": "Unit", "qty": "Qty", "rate": "Rate", "amount": "Amount", "certified_qty": "Certified",
        "done_pct": "Done", "add_line": "+ Add item", "save": "Save", "delete": "Delete", "total": "Total", "none": "None.",
        "terms": "Contract terms", "retention_rate": "Retention %", "recovery_rate": "Advance recovered from each certificate %",
        "terms_hint": "Retention is held from each certificate until handover; the advance is recovered from each certificate at this rate until it is paid back.",
        "saved": "Saved.", "deleted": "Deleted.",
        "certificates_intro": "Each certificate makes a draft sales invoice for the work value; post it from the invoice screen. Retention and the advance come off what is due.",
        "number": "No.", "date": "Date", "gross": "Work value", "tax": "Tax", "invoice_total": "Invoice total", "retention": "Retention",
        "recovery": "Advance recovered", "net": "Net payable", "status": "Status", "invoice": "Invoice",
        "new_certificate": "New certificate", "cumulative": "Qty to date", "previous": "Previous", "current": "This certificate", "contract_qty": "Contract qty",
        "lump_amount": "Value of work done", "lump_description": "Description (e.g. first month's works)", "make_certificate": "Make the certificate",
        "certificate_made": "Certificate {number} made as a draft ({invoice}). Review it and post the invoice.", "draft_open": "A draft certificate must be posted or withdrawn first.",
        "certificate": "Certificate {number}", "open_invoice": "Open the invoice", "print": "Print the certificate", "withdraw": "Withdraw the draft",
        "withdrawn": "The certificate and its draft were withdrawn.", "previous_value": "Previous value", "current_value": "This value", "cumulative_value": "Value to date",
        "over_qty": "Quantity to date is above the contract quantity", "posted_note": "The invoice is posted: correct it with a return or a cancellation on the invoice screen.",
        "payments_intro": "Money from the owner is an ordinary collection on their account, marked for this project. The advance is recovered from certificates.",
        "receive": "Receive from the owner", "kind": "Kind", "cashbox": "Cashbox", "receive_btn": "Record it", "received": "{number} recorded.",
        "link_payment": "Link a recorded collection", "link": "Link", "unlink": "Unlink", "linked": "Linked.", "unlinked": "Unlinked.",
        "retention_title": "Retention", "release": "Release retention", "release_btn": "Record the release", "released": "Release recorded.", "reverse_release": "Reverse the release", "release_reversed": "Release reversed.", "reversed_on": "Reversed on",
        "release_hint": "At handover: a release moves the amount from held to due from the owner; the collection itself is recorded as usual afterwards.",
        "notes": "Notes", "held_total": "Held", "released_total": "Released",
        "subcontracts_intro": "Work given to subcontractors (suppliers). Each subcontractor bill makes a draft purchase invoice, and we hold retention from it.",
        "supplier": "Subcontractor", "scope": "Work given", "value": "Contract value", "new_subcontract": "Give work to a subcontractor", "billed": "Billed",
        "remaining": "Remaining", "bill": "Subcontractor bill", "bill_btn": "Make the bill", "billed_ok": "Draft {invoice} made; post it from the purchases screen.",
        "other_purchases": "Other service purchases for the project", "purchases_hint": "Service invoices (equipment hire, transport, site services). Stock materials are issued from stock.",
        "link_purchase": "Link a service purchase", "vendor": "Supplier", "heading": "Heading", "drafts": "Drafts",
        "budget_intro": "The planned cost per heading against the actual cost from materials issued, expenses and linked purchases.",
        "budget": "Budget", "actual": "Actual", "variance": "Variance", "used": "Used", "save_budget": "Save the budget",
        "view_only": "View only: changes need the permission.",
    },
}


def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _context(request, project, tab, **extra):
    from .services import OPEN

    lang = _lang(request)
    context = _base(request, project=project, tab=tab, cwords=WORDS[lang], position=contract.position(project), is_open=project.status in OPEN,
                    can_collect=user_has_permission(request.user, COLLECT), can_buy=user_has_permission(request.user, BUY),
                    can_view_purchases=user_has_permission(request.user, BUY_VIEW))
    context["page_title"] = f"{project.name} — {WORDS[lang]['tab_' + tab]}"
    context.update(extra)
    return context


def _back(project, name, lang, **kwargs):
    return redirect(f"{reverse(name, args=[project.pk, *kwargs.values()])}?lang={lang}")


def _error(exc, lang):
    return translate(" ".join(exc.messages), lang)


def _scoped(queryset, raw):
    """A posted id, looked up only among what this entity may use."""

    raw = str(raw or "")
    return queryset.filter(pk=raw).first() if raw.isdigit() else None


def _need(request, code):
    if not user_has_permission(request.user, code):
        raise PermissionDenied(f"This action needs {code}.")


# ---- bill of quantities ----

@require_permission(VIEW)
def boq(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error = ""
    if request.method == "POST":
        _need(request, MANAGE)
        action = request.POST.get("action", "")
        try:
            if action == "terms":
                contract.save_terms(project, request.user, retention_rate=request.POST.get("retention_rate"),
                                    advance_recovery_rate=request.POST.get("advance_recovery_rate"), lang=lang)
            elif action in ("add", "edit"):
                line = _pick(project.boq.model, request.POST.get("line"), project=project) if action == "edit" else None
                if action == "edit" and line is None:
                    raise Http404
                contract.save_boq_line(project, request.user, request.POST, line, lang)
            elif action == "delete":
                line = _pick(project.boq.model, request.POST.get("line"), project=project)
                if line is None:
                    raise Http404
                contract.delete_boq_line(project, line, request.user, lang)
                messages.success(request, words["deleted"])
                return _back(project, "projects:boq", lang)
            else:
                raise Http404
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["saved"])
            return _back(project, "projects:boq", lang)
    rows = []
    for line in project.boq.all():
        certified = contract.certified_quantity(line)
        rows.append({"line": line, "amount": contract.line_amount(line), "certified": certified,
                     "done": int(min(certified / line.quantity * 100, Decimal("999"))) if line.quantity else 0})
    return render(request, "projects/boq.html", _context(request, project, "boq", rows=rows, total=contract.boq_total(project), error=error))


# ---- certificates ----

@require_permission(VIEW)
def certificates(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error, posted = "", {}
    if request.method == "POST":
        _need(request, MANAGE)
        posted = request.POST
        quantities = {line.pk: request.POST.get(f"q_{line.pk}", "") for line in project.boq.all()}
        try:
            certificate = contract.create_certificate(
                project, request.user, quantities={k: v for k, v in quantities.items() if v not in ("", None)} or None,
                amount=request.POST.get("amount"), description=request.POST.get("description", ""),
                certificate_date=_date(request.POST.get("certificate_date")), lang=lang,
            )
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["certificate_made"].format(number=certificate.number, invoice=certificate.invoice.invoice_number))
            return _back(project, "projects:certificate", lang, number=certificate.number)
    rows = [{"certificate": c, "net": contract.net_payable(c), "status_label": choice_label(c.invoice, "status", lang)}
            for c in project.certificates.select_related("invoice")]
    boq_rows = []
    for line in project.boq.all():
        previous = contract.certified_quantity(line)
        boq_rows.append({"line": line, "previous": previous, "value": posted.get(f"q_{line.pk}", "")})
    draft = contract.live_certificates(project).filter(invoice__status="draft").first()
    return render(request, "projects/certificates.html", _context(
        request, project, "certificates", rows=rows, boq_rows=boq_rows, draft=draft, error=error, posted=posted,
        today=timezone.localdate().isoformat(),
    ))


def _certificate(project, number):
    return get_object_or_404(Certificate.objects.select_related("invoice", "invoice__customer"), project=project, number=number)


@require_permission(VIEW)
def certificate_detail(request, pk, number):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    certificate = _certificate(project, number)
    error = ""
    if request.method == "POST":
        _need(request, MANAGE)
        if request.POST.get("action") != "withdraw":
            raise Http404
        try:
            contract.withdraw_certificate(project, certificate, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["withdrawn"])
            return _back(project, "projects:certificates", lang)
    return render(request, "projects/certificate.html", _context(
        request, project, "certificates", certificate=certificate, lines=contract.certificate_rows(certificate), net=contract.net_payable(certificate),
        status_label=choice_label(certificate.invoice, "status", lang), error=error,
    ))


@require_permission(VIEW)
def certificate_print(request, pk, number):
    from printing.amount_words import amount_in_words
    from printing.company import company_details

    lang = _lang(request)
    project = _project(pk)
    certificate = _certificate(project, number)
    company = company_details()
    net = contract.net_payable(certificate)
    context = _context(request, project, "certificates", certificate=certificate, lines=contract.certificate_rows(certificate), net=net,
                       status_label=choice_label(certificate.invoice, "status", lang), company=company,
                       net_words=amount_in_words(net, company["currency"], lang))
    return render(request, "projects/certificate_print.html", context)


# ---- payments and retention ----

@require_permission(VIEW)
def payments(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "receive":
                _need(request, COLLECT)
                payment = contract.receive_payment(project, request.user, kind=request.POST.get("kind"),
                                                   cashbox=_scoped(entity_scope.cashboxes(Cashbox.objects).filter(active=True), request.POST.get("cashbox")),
                                                   amount=request.POST.get("amount"),
                                                   payment_date=_date(request.POST.get("payment_date")), lang=lang)
                messages.success(request, words["received"].format(number=payment.payment_number))
            elif action == "link":
                _need(request, COLLECT)
                payment = _scoped(entity_scope.scope(CustomerPayment.objects, entity_scope.CUSTOMER_PAYMENT), request.POST.get("payment"))
                if payment is None:
                    raise ValidationError(contract.MESSAGES[lang]["payment_status"])
                contract.link_payment(project, payment, request.user, kind=request.POST.get("kind"), lang=lang)
                messages.success(request, words["linked"])
            elif action == "unlink":
                _need(request, COLLECT)
                link = _pick(ProjectPayment, request.POST.get("link"), project=project)
                if link is None:
                    raise Http404
                contract.unlink_payment(project, link, request.user, lang)
                messages.success(request, words["unlinked"])
            elif action == "release":
                _need(request, MANAGE)
                contract.release_retention(project, request.user, amount=request.POST.get("amount"), release_date=_date(request.POST.get("release_date")),
                                           notes=request.POST.get("notes", ""), lang=lang)
                messages.success(request, words["released"])
            elif action == "reverse_release":
                _need(request, MANAGE)
                release = _pick(RetentionRelease, request.POST.get("release"), project=project)
                if release is None:
                    raise Http404
                contract.reverse_release(project, release, request.user, lang=lang)
                messages.success(request, words["release_reversed"])
            else:
                raise Http404
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _back(project, "projects:payments", lang)
    links = project.payments.select_related("payment", "payment__cashbox")
    return render(request, "projects/payments.html", _context(
        request, project, "payments", error=error, today=timezone.localdate().isoformat(),
        links=[{"link": link, "kind_label": choice_label(link, "kind", lang), "status_label": choice_label(link.payment, "status", lang)} for link in links],
        kinds=localized_choices(ProjectPayment, "kind", lang),
        cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True),
        free_payments=entity_scope.scope(CustomerPayment.objects, entity_scope.CUSTOMER_PAYMENT).filter(customer=project.customer, status="posted", project_payment__isnull=True).order_by("-payment_date")[:50],
        releases=project.retention_releases.all(),
    ))


# ---- subcontractors and service purchases ----

@require_permission(BUY_VIEW)
def subcontracts(request, pk):
    from settings_core.module_gate import closed_module

    if closed_module(reverse("purchases:list")) is not None:
        raise Http404
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error = ""
    if request.method == "POST":
        _need(request, BUY)
        action = request.POST.get("action", "")
        try:
            if action == "new":
                costs.save_subcontract(project, request.user, {"supplier": _pick(Supplier, request.POST.get("supplier"), active=True),
                                                               "scope": request.POST.get("scope"), "value": request.POST.get("value"),
                                                               "retention_rate": request.POST.get("retention_rate")}, lang=lang)
                messages.success(request, words["saved"])
            elif action in ("bill", "release"):
                subcontract = _pick(Subcontract, request.POST.get("subcontract"), project=project)
                if subcontract is None:
                    raise Http404
                if action == "bill":
                    bill = costs.bill_subcontract(subcontract, request.user, amount=request.POST.get("amount"), description=request.POST.get("description", ""),
                                                  bill_date=_date(request.POST.get("bill_date")), lang=lang)
                    messages.success(request, words["billed_ok"].format(invoice=bill.invoice.invoice_number))
                else:
                    costs.release_subcontract_retention(subcontract, request.user, amount=request.POST.get("amount"),
                                                        release_date=_date(request.POST.get("release_date")), notes=request.POST.get("notes", ""), lang=lang)
                    messages.success(request, words["released"])
            elif action == "reverse_release":
                release = _pick(SubcontractRelease, request.POST.get("release"), subcontract__project=project)
                if release is None:
                    raise Http404
                costs.reverse_subcontract_release(release, request.user, lang=lang)
                messages.success(request, words["release_reversed"])
            elif action == "withdraw":
                bill = _pick(SubcontractBill, request.POST.get("bill"), subcontract__project=project)
                if bill is None:
                    raise Http404
                costs.withdraw_bill(bill, request.user, lang)
                messages.success(request, words["withdrawn"])
            elif action == "link_purchase":
                invoice = _scoped(entity_scope.scope(PurchaseInvoice.objects, entity_scope.PURCHASE_INVOICE), request.POST.get("invoice"))
                if invoice is None:
                    raise ValidationError(costs.MESSAGES[lang]["cancelled"])
                costs.link_purchase(project, invoice, request.user, heading=request.POST.get("heading"), lang=lang)
                messages.success(request, words["linked"])
            elif action == "unlink_purchase":
                link = _pick(ProjectPurchase, request.POST.get("link"), project=project)
                if link is None:
                    raise Http404
                costs.unlink_purchase(project, link, request.user, lang)
                messages.success(request, words["unlinked"])
            else:
                raise Http404
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return _back(project, "projects:subcontracts", lang)
    subs = []
    for subcontract in project.subcontracts.select_related("supplier"):
        bills = [{"bill": bill, "net": costs.bill_net_payable(bill), "status_label": choice_label(bill.invoice, "status", lang)}
                 for bill in subcontract.bills.select_related("invoice")]
        subs.append({"subcontract": subcontract, "figures": costs.subcontract_figures(subcontract), "bills": bills, "releases": subcontract.releases.all()})
    others = [{"link": link, "heading_label": choice_label(link, "heading", lang), "net": costs.net_purchase(link.invoice),
               "status_label": choice_label(link.invoice, "status", lang)}
              for link in project.purchases.select_related("invoice", "invoice__supplier").filter(invoice__subcontract_bill__isnull=True)]
    free = entity_scope.scope(PurchaseInvoice.objects, entity_scope.PURCHASE_INVOICE).exclude(status="cancelled").filter(project_purchase__isnull=True).exclude(lines__item__is_stock_tracked=True).distinct().order_by("-invoice_date")[:50]
    return render(request, "projects/subcontracts.html", _context(
        request, project, "subcontracts", subs=subs, others=others, free_purchases=free, error=error, today=timezone.localdate().isoformat(),
        suppliers=Supplier.objects.filter(active=True).order_by("name"), headings=localized_choices(ProjectPurchase, "heading", lang),
    ))


# ---- budget ----

@require_permission(PROFIT)
def budget(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    error = ""
    if request.method == "POST":
        _need(request, MANAGE)
        try:
            costs.save_budget(project, request.user, {heading: request.POST.get(f"b_{heading}", "0") for heading in CostHeading.values}, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, words["saved"])
            return _back(project, "projects:budget", lang)
    planned = {line.heading: line.amount for line in project.budget.all()}
    actual = costs.actual_by_heading(project)
    fields = [{"heading": value, "label": label, "amount": planned.get(value, ""), "actual": actual.get(value, Decimal("0"))}
              for value, label in localized_choices(BudgetLine, "heading", lang)]
    return render(request, "projects/budget.html", _context(request, project, "budget", table=costs.budget_rows(project, lang), fields=fields, error=error))
