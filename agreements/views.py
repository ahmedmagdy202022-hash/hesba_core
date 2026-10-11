"""SVC-002 screens: the contracts list (with "bill what is due") and one contract."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item
from permissions.services import user_has_permission

from . import services
from .models import ContractStatus, Every, ServiceContract

WORDS = {
    "ar": {"title": "عقود الخدمة", "intro": "عقود الصيانة والاشتراكات اللي بتتفوتر كل فترة. «فوتر المستحق» بيعمل فاتورة بيع (مسودة) لكل فترة جت ولسه ماتفوترتش، ومش بيفوتر نفس الفترة مرتين.",
           "number": "رقم العقد", "customer": "العميل", "service": "الخدمة", "description": "الوصف", "amount": "قيمة الفترة", "every": "بيتفوتر",
           "starts": "من", "ends": "لحد (اختياري)", "visits": "زيارات في الفترة", "status": "الحالة", "notes": "ملاحظات", "next": "الفترة الجاية",
           "due": "مستحق دلوقتي", "unpaid": "رصيد العميل", "billing_from": "فوتر من (اختياري)", "bill_due": "فوتر المستحق ({n} فترة)", "new": "عقد جديد", "save": "حفظ",
           "none": "مفيش عقود لسه.", "back": "عقود الخدمة", "bills": "الفترات اللي اتفوترت", "period": "الفترة", "invoice": "الفاتورة",
           "invoice_status": "حالة الفاتورة", "this_period": "الفترة الحالية", "done_visits": "زيارات اتعملت", "of": "من", "choose": "اختار",
           "saved": "اتحفظ العقد.", "billed": "اتعملت {n} فاتورة (مسودة). رحّلها من صفحة الفواتير.", "edit": "تعديل العقد", "no_bills": "لسه مفيش فواتير.",
           "draft": "مسودة", "posted": "مترحّلة", "cancelled": "اتلغت", "view_only": "إضافة العقود وفوترتها للي عنده صلاحية إنشاء فاتورة بيع بس."},
    "en": {"title": "Service contracts", "intro": "Maintenance contracts and retainers billed every period. “Bill what is due” makes a draft sales invoice for each period that has come and is not billed yet, and never bills a period twice.",
           "number": "Contract", "customer": "Customer", "service": "Service", "description": "Description", "amount": "Amount per period", "every": "Billed",
           "starts": "From", "ends": "Until (optional)", "visits": "Visits per period", "status": "Status", "notes": "Notes", "next": "Next period",
           "due": "Due now", "unpaid": "Customer balance", "billing_from": "Bill from (optional)", "bill_due": "Bill what is due ({n} periods)", "new": "New contract", "save": "Save",
           "none": "No contracts yet.", "back": "Service contracts", "bills": "Billed periods", "period": "Period", "invoice": "Invoice",
           "invoice_status": "Invoice status", "this_period": "This period", "done_visits": "Visits done", "of": "of", "choose": "Choose",
           "saved": "Contract saved.", "billed": "{n} draft invoices made. Post them from the invoices page.", "edit": "Edit the contract", "no_bills": "No invoices yet.",
           "draft": "Draft", "posted": "Posted", "cancelled": "Cancelled", "view_only": "Adding and billing contracts needs the create-sales-invoice permission."},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request):
    if not services.is_contract_business():
        raise Http404("Service contracts are for service businesses.")
    if not user_has_permission(request.user, services.VIEW):
        raise PermissionDenied("Service contracts need sales.view_sales_invoices.")


def _choices(lang):
    return {"everies": [(value, services.EVERY_LABELS[lang][value]) for value in Every.values],
            "statuses": [(value, services.STATUS_LABELS[lang][value]) for value in ContractStatus.values]}


def _base(request, lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"],
            "can_manage": user_has_permission(request.user, services.MANAGE), **_lists(extra.get("contract")), **_choices(lang), **extra}


def _lists(contract):
    from django.db.models import Q

    keep_customer = Q(pk=contract.customer_id) if contract else Q(pk__in=[])
    keep_service = Q(pk=contract.service_id) if contract else Q(pk__in=[])
    return {"customers": Customer.objects.filter(Q(active=True) | keep_customer).exclude(customer_code="WALK-IN").order_by("name"),
            "services_list": Item.objects.filter(Q(active=True, is_stock_tracked=False) | keep_service).order_by("item_name")}


def _id(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _data(request, contract=None):
    """The posted contract. Editing keeps its own customer and service even if they were made inactive since, so it can still be ended."""

    from django.db.models import Q

    data = request.POST.dict()
    keep_customer = Q(pk=contract.customer_id) if contract else Q(pk__in=[])
    keep_service = Q(pk=contract.service_id) if contract else Q(pk__in=[])
    data["customer"] = Customer.objects.filter(Q(active=True) | keep_customer).filter(pk=_id(request.POST.get("customer"))).first()
    data["service"] = Item.objects.filter(Q(active=True, is_stock_tracked=False) | keep_service).filter(pk=_id(request.POST.get("service"))).first()
    return data


def contract_list(request):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "bill_due":
                bills = services.bill_due(request.user, lang=lang)
                messages.success(request, words["billed"].format(n=len(bills)))
                return redirect(f"{reverse('agreements:list')}?lang={lang}")
            contract = services.save_contract(_data(request), request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('agreements:detail', args=[contract.pk])}?lang={lang}")
    today = timezone.localdate()
    rows = []
    for contract in services.in_scope(ServiceContract.objects.select_related("customer", "service"))[:300]:
        rows.append({"contract": contract, "overview": services.overview(contract, today), "unpaid": services.unpaid(contract),
                     "every_label": services.EVERY_LABELS[lang][contract.every], "status_label": services.STATUS_LABELS[lang][contract.status]})
    due = services.due_count(today)  # every contract the button bills, not only the rows shown
    return render(request, "agreements/list.html", _base(request, lang, rows=rows, due=due, bill_due_label=words["bill_due"].format(n=due), error=error, post=request.POST if error else {},
                                                       today=today))


def contract_detail(request, pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    contract = get_object_or_404(services.in_scope(ServiceContract.objects.select_related("customer", "service")), pk=pk)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "bill_due":
                bills = services.bill_due(request.user, contracts=[contract], lang=lang)
                messages.success(request, words["billed"].format(n=len(bills)))
            else:
                services.save_contract(_data(request, contract), request.user, contract, lang)
                messages.success(request, words["saved"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('agreements:detail', args=[contract.pk])}?lang={lang}")
    overview = services.overview(contract)
    bills = list(contract.bills.select_related("invoice"))
    for bill in bills:
        bill.end = services.period_end(contract, bill.period_start)
        bill.status_label = words.get(bill.invoice.status, bill.invoice.status)
    return render(request, "agreements/detail.html", _base(request, lang, contract=contract, overview=overview, bills=bills, today=timezone.localdate(),
                                                         bill_due_label=words["bill_due"].format(n=len(overview["due"])),
                                                         unpaid=services.unpaid(contract), error=error, post=request.POST if error else {},
                                                         every_label=services.EVERY_LABELS[lang][contract.every],
                                                         status_label=services.STATUS_LABELS[lang][contract.status]))
