"""Contracting C11 (HG-128): one subcontract's page (its lines, bills by quantities, the advance and payments) and the
printed subcontractor statement. The permissions are the subcontracts tab's own: reading needs
``purchases.view_purchase_invoices``; lines, terms and bills need ``purchases.create_purchase_invoice``; an advance or
a payment needs ``purchases.pay_supplier`` (the supplier payment screen's permission)."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from entities import scope as entity_scope
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.display_labels import choice_label, localized_choices

from . import costs, subcontracting
from .contract_views import BUY, BUY_VIEW, _back, _context, _error, _need, _project, _scoped
from .models import Subcontract, SubcontractLine, SubcontractPayment
from .views import _date, _lang, _pick

PAY = "purchases.pay_supplier"

WORDS = {
    "ar": {
        "back": "مقاولو الباطن", "intro": "بنود مقاول الباطن بالكمية والفئة، ومستخلصاته بالكميات المنفذة حتى تاريخه، ودفعته المقدمة اللي بتتخصم من كل مستخلص.",
        "lines_total": "مجموع البنود", "advance": "دفعة مقدمة اتدفعت", "recovered": "اتخصم من المستخلصات", "advance_left": "دفعة مقدمة لسه ما اتخصمتش",
        "paid": "اتدفع من المستخلصات", "terms": "شروط العقد", "recovery_rate": "نسبة استرداد الدفعة المقدمة من كل مستخلص %",
        "terms_hint": "قيمة العقد هي الحد الأقصى للمستخلصات؛ لو فيه أعمال زيادة زوّد القيمة هنا الأول.",
        "lines": "بنود مقاول الباطن", "line_no": "رقم", "client_item": "بند المالك", "no_client_item": "من غير بند للمالك", "billed_qty": "اتعمل بيه مستخلصات",
        "owner_certified": "مستخلص للمالك", "ahead": "أكتر من اللي اتعمل بيه مستخلص للمالك", "add_line": "إضافة بند", "lines_none": "مفيش بنود لسه. من غير بنود المستخلص بيبقى بمبلغ مقطوع من صفحة مقاولي الباطن.",
        "bill_title": "مستخلص جديد لمقاول الباطن", "bill_hint": "اكتب الكمية المنفذة حتى تاريخه لكل بند (مش كمية الفترة دي بس)؛ المستخلص بيفوتر الفرق.",
        "bills": "مستخلصات مقاول الباطن", "print": "اطبع المستخلص", "payments": "الدفعات لمقاول الباطن", "pay_title": "دفعة لمقاول الباطن",
        "pay_hint": "الدفعة المقدمة بتتسجل سداد عادي للمورد وبتتخصم من مستخلصاته بالنسبة اللي في الشروط. السداد من المستخلصات بيتسجل هنا كمان عشان يبان في كشف مقاول الباطن.",
        "pay_btn": "سجّل الدفعة", "paid_ok": "اتسجلت الدفعة {number}.", "payment_no": "رقم السداد", "billed_note": "اتعمل المستخلص كمسودة ({invoice}). راجعه ورحّله من شاشة المشتريات.",
        "draft_open": "فيه مستخلص مسودة لازم يترحّل أو يتسحب الأول.", "cancel_hint": "إلغاء أي دفعة بيتم من شاشة مدفوعات الموردين.",
    },
    "en": {
        "back": "Subcontractors", "intro": "The subcontract's lines by quantity and rate, its bills by quantities done to date, and the advance paid that each bill recovers.",
        "lines_total": "Lines total", "advance": "Advance paid", "recovered": "Recovered by bills", "advance_left": "Advance not yet recovered",
        "paid": "Paid against bills", "terms": "Contract terms", "recovery_rate": "Advance recovered from each bill %",
        "terms_hint": "The value is the most the bills may reach; raise it here first if there is extra work.",
        "lines": "Subcontract lines", "line_no": "No.", "client_item": "Owner's item", "no_client_item": "No owner's item", "billed_qty": "Billed",
        "owner_certified": "Certified to the owner", "ahead": "More than the owner has been certified for", "add_line": "Add a line", "lines_none": "No lines yet. Without lines, bills are lump sums from the subcontractors tab.",
        "bill_title": "New subcontractor bill", "bill_hint": "Type the quantity done to date for each line (not this period's alone); the bill charges the difference.",
        "bills": "Subcontractor bills", "print": "Print the bill", "payments": "Payments to the subcontractor", "pay_title": "Pay the subcontractor",
        "pay_hint": "An advance is an ordinary supplier payment, recovered from each bill at the rate in the terms. Payments against bills are recorded here too, so they show on the subcontractor's statement.",
        "pay_btn": "Record the payment", "paid_ok": "Payment {number} recorded.", "payment_no": "Payment no.", "billed_note": "Bill made as a draft ({invoice}). Review it and post it from the purchases screen.",
        "draft_open": "A draft bill must be posted or withdrawn first.", "cancel_hint": "Any payment is cancelled from the supplier payments screen.",
    },
}


def _subcontract(project, sid):
    return get_object_or_404(Subcontract.objects.select_related("supplier", "project"), pk=sid, project=project)


def _gate():
    from settings_core.module_gate import closed_module

    if closed_module(reverse("purchases:list")) is not None:
        raise Http404


def _bills(subcontract):
    return costs._here(subcontract.bills.select_related("invoice"))


@require_permission(BUY_VIEW)
def subcontract_page(request, pk, sid):
    _gate()
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    subcontract = _subcontract(project, sid)
    error, posted = "", {}
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "terms":
                _need(request, BUY)
                costs.save_subcontract(project, request.user, {"supplier": subcontract.supplier, "scope": request.POST.get("scope") or subcontract.scope,
                                                               "value": request.POST.get("value"), "retention_rate": request.POST.get("retention_rate"),
                                                               "advance_recovery_rate": request.POST.get("advance_recovery_rate")}, subcontract, lang=lang)
            elif action == "add_line":
                _need(request, BUY)
                posted = request.POST
                subcontracting.save_line(subcontract, request.user, request.POST, lang=lang)
            elif action == "delete_line":
                _need(request, BUY)
                line = _pick(SubcontractLine, request.POST.get("line"), subcontract=subcontract)
                if line is None:
                    raise Http404
                subcontracting.delete_line(subcontract, line, request.user, lang)
            elif action == "bill":
                _need(request, BUY)
                posted = request.POST
                quantities = {line.pk: request.POST.get(f"q_{line.pk}", "") for line in subcontract.lines.all()}
                bill = subcontracting.bill_by_quantities(subcontract, request.user, quantities={k: v for k, v in quantities.items() if v not in ("", None)},
                                                         description=request.POST.get("description", ""), bill_date=_date(request.POST.get("bill_date")),
                                                         lang=lang)
                messages.success(request, words["billed_note"].format(invoice=bill.invoice.invoice_number))
                for warning in bill.warnings:
                    messages.warning(request, warning)
            elif action == "pay":
                _need(request, PAY)
                posted = request.POST
                payment = subcontracting.pay(subcontract, request.user, kind=request.POST.get("kind"),
                                             cashbox=_scoped(entity_scope.cashboxes(Cashbox.objects).filter(active=True), request.POST.get("cashbox")),
                                             amount=request.POST.get("amount"), payment_date=_date(request.POST.get("payment_date")),
                                             notes=request.POST.get("notes", ""), lang=lang)
                messages.success(request, words["paid_ok"].format(number=payment.payment_number))
            elif action == "withdraw":
                _need(request, BUY)
                bill = _scoped(_bills(subcontract), request.POST.get("bill"))
                if bill is None:
                    raise Http404
                costs.withdraw_bill(bill, request.user, lang)
            else:
                raise Http404
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            if action not in ("bill", "pay"):
                from .contract_views import WORDS as CWORDS

                messages.success(request, CWORDS[lang]["saved"])
            return _back(project, "projects:subcontract", lang, sid=subcontract.pk)
    subcontract = _subcontract(project, sid)
    bills = []
    for bill in _bills(subcontract):
        bills.append({"bill": bill, "net": costs.bill_net_payable(bill), "status_label": choice_label(bill.invoice, "status", lang),
                      "warnings": subcontracting.bill_warnings(bill, lang) if bill.invoice.status != "cancelled" else []})
    payments = entity_scope.scope(subcontract.payments.select_related("payment", "payment__cashbox"), "payment__" + entity_scope.SUPPLIER_PAYMENT)
    draft = _bills(subcontract).filter(invoice__status="draft").first()
    line_rows = subcontracting.line_rows(subcontract)
    for row in line_rows:
        row["value"] = posted.get(f"q_{row['line'].pk}", "") if posted.get("action") == "bill" else ""
    return render(request, "projects/subcontract.html", _context(
        request, project, "subcontracts", sub=subcontract, swords=words, error=error, posted=posted, today=timezone.localdate().isoformat(),
        figures=costs.subcontract_figures(subcontract), sfigures=subcontracting.figures(subcontract), line_rows=line_rows, bills=bills, draft=draft,
        payments=[{"link": link, "kind_label": choice_label(link, "kind", lang), "status_label": choice_label(link.payment, "status", lang)} for link in payments],
        kinds=localized_choices(SubcontractPayment, "kind", lang), cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True),
        boq_lines=project.boq.all(), can_pay=user_has_permission(request.user, PAY),
    ))


@require_permission(BUY_VIEW)
def bill_print(request, pk, sid, number):
    """The printed subcontractor statement: the client certificate's layout, with the subcontractor's name."""

    from printing.amount_words import amount_in_words
    from printing.company import company_details

    _gate()
    lang = _lang(request)
    project = _project(pk)
    subcontract = _subcontract(project, sid)
    bill = get_object_or_404(_bills(subcontract).select_related("invoice", "invoice__supplier"), number=number)
    company = company_details()
    net = costs.bill_net_payable(bill)
    return render(request, "projects/subcontract_bill_print.html", _context(
        request, project, "subcontracts", sub=subcontract, bill=bill, rows=subcontracting.bill_rows(bill), net=net, swords=WORDS[lang],
        status_label=choice_label(bill.invoice, "status", lang), company=company, net_words=amount_in_words(net, company["currency"], lang),
        warnings=subcontracting.bill_warnings(bill, lang),
    ))

