"""SVC-001 screens: the orders board, receiving a device, one order, the intake receipt, and the technicians' report."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Item

from . import services
from .models import OPEN, Status, WorkOrder, WorkOrderLine

WORDS = {
    "ar": {
        "title": "أوامر الشغل", "intro": "كل جهاز أو طلب بيدخل المحل: اتستلم إمتى، مع مين، وصل لفين، ومين هيستلمه.",
        "new": "+ استلام جهاز / طلب جديد", "search": "دوّر برقم الأمر أو العميل أو التليفون أو السيريال", "find": "بحث",
        "f_open": "المفتوحة", "f_ready": "جاهزة للتسليم", "f_late": "متأخرة", "f_delivered": "اتسلّمت", "f_all": "الكل",
        "number": "رقم الأمر", "customer": "العميل", "device": "الجهاز / الخدمة", "serial": "السيريال / IMEI", "status": "الحالة", "technician": "الفني",
        "received": "اتستلم", "promised": "ميعاد التسليم", "open": "افتح", "none": "مفيش أوامر هنا.", "late": "متأخر",
        "accessories": "اتسلّم معاه (شاحن، جراب…)", "problem": "العطل / المطلوب", "warranty_days": "ضمان بعد التسليم (يوم)", "notes": "ملاحظات",
        "customer_existing": "عميل موجود", "choose": "اختار", "customer_new": "أو عميل جديد: الاسم", "customer_phone": "تليفون العميل", "save": "حفظ",
        "receive_btn": "استلام وطباعة الإيصال", "back": "أوامر الشغل", "timeline": "اللي حصل", "diagnosis": "التشخيص / اللي اتعمل",
        "details": "بيانات الشغل", "lines": "قطع الغيار والمصنعية", "item": "الصنف / الخدمة", "qty": "الكمية", "price": "السعر", "amount": "القيمة",
        "warranty": "ضمان (ببلاش)", "add": "ضيف", "remove": "شيل", "parts": "قطع الغيار", "labour": "المصنعية", "total": "الإجمالي", "tax": "الضريبة",
        "no_lines": "لسه مفيش قطع ولا مصنعية على الأمر.", "next": "انقل الأمر لـ", "deliver": "تسليم للعميل", "paid_now": "المدفوع دلوقتي",
        "cashbox": "الخزنة", "deliver_btn": "سلّم واعمل الفاتورة", "delivered_ok": "اتسلّم الأمر {number}.", "invoice": "الفاتورة",
        "receipt": "اطبع إيصال الاستلام", "warranty_until": "ضمان لحد", "warranty_return": "رجع في الضمان", "warranty_of": "ضمان على أمر",
        "saved": "اتحفظ.", "moved": "اتنقل الأمر لـ «{status}».", "received_ok": "اتستلم الأمر {number}.", "reserved": "محجوز على أوامر مفتوحة",
        "report": "تقرير الفنيين", "report_intro": "الأوامر اللي اتسلمت في الشهر لكل فني: عددها، وإيرادها قبل الضريبة، ومتوسط الأيام في المحل، واللي رجع في الضمان.",
        "month": "الشهر", "go": "عرض", "count": "اتسلّم", "billed": "الإيراد", "avg_days": "متوسط الأيام", "returns": "رجع في الضمان",
        "late_list": "متأخر عن ميعاده", "nobody": "من غير فني", "terms": "الجهاز بيتسلّم بالإيصال ده. الأجهزة اللي متستلمش خلال 90 يوم من «جاهز» المحل مش مسؤول عنها.",
        "signature": "توقيع العميل", "note": "ملاحظة", "view_only": "تقدر تشوف الأمر بس.",
    },
    "en": {
        "title": "Work orders", "intro": "Every device or request that comes in: when it was received, who has it, where it stands, and who collects it.",
        "new": "+ Receive a device / request", "search": "Search by order number, customer, phone or serial", "find": "Search",
        "f_open": "Open", "f_ready": "Ready for pickup", "f_late": "Late", "f_delivered": "Delivered", "f_all": "All",
        "number": "Order", "customer": "Customer", "device": "Device / service", "serial": "Serial / IMEI", "status": "Status", "technician": "Technician",
        "received": "Received", "promised": "Promised for", "open": "Open", "none": "No orders here.", "late": "late",
        "accessories": "Handed in with it (charger, case…)", "problem": "Fault / what is needed", "warranty_days": "Warranty after delivery (days)", "notes": "Notes",
        "customer_existing": "Existing customer", "choose": "Choose", "customer_new": "or a new customer: name", "customer_phone": "Customer's phone", "save": "Save",
        "receive_btn": "Receive and print the receipt", "back": "Work orders", "timeline": "What happened", "diagnosis": "Diagnosis / work done",
        "details": "Job details", "lines": "Parts and labour", "item": "Item / service", "qty": "Qty", "price": "Price", "amount": "Amount",
        "warranty": "Warranty (free)", "add": "Add", "remove": "Remove", "parts": "Parts", "labour": "Labour", "total": "Total", "tax": "VAT",
        "no_lines": "No parts or labour on the order yet.", "next": "Move the order to", "deliver": "Hand back to the customer", "paid_now": "Paid now",
        "cashbox": "Cashbox", "deliver_btn": "Deliver and invoice", "delivered_ok": "Order {number} delivered.", "invoice": "Invoice",
        "receipt": "Print the intake receipt", "warranty_until": "Warranty until", "warranty_return": "Back under warranty", "warranty_of": "Warranty on order",
        "saved": "Saved.", "moved": "Order moved to “{status}”.", "received_ok": "Order {number} received.", "reserved": "reserved on open orders",
        "report": "Technicians", "report_intro": "Orders delivered in the month per technician: how many, revenue before VAT, average days in the shop, and warranty returns.",
        "month": "Month", "go": "Show", "count": "Delivered", "billed": "Revenue", "avg_days": "Avg. days", "returns": "Warranty returns",
        "late_list": "Past the promised date", "nobody": "No technician", "terms": "The device is collected with this receipt. The shop is not responsible for devices left more than 90 days after they are ready.",
        "signature": "Customer's signature", "note": "Note", "view_only": "You can view this order only.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request):
    if not services.is_workshop():
        raise Http404("Work orders are for service workshops.")
    if not (services.can_view(request.user) or services.technician_of(request.user) is not None):
        raise PermissionDenied("Work orders need sales.view_sales_invoices.")


def _base(request, lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "section": "workorders",
            "labels": services.STATUS_LABELS[lang], "can_manage": services.can_manage(request.user), "can_view": services.can_view(request.user), **extra}


def _back(lang, order=None):
    url = reverse("workorders:detail", args=[order.pk]) if order else reverse("workorders:list")
    return redirect(f"{url}?lang={lang}")


def _technicians():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


def board(request):
    _guard(request)
    services.sync_cancelled(request.user)  # a delivery whose invoice was cancelled is open again
    lang = _lang(request)
    today = timezone.localdate()
    show = request.GET.get("show") or "open"
    rows = services.visible_to(request.user).select_related("customer", "technician")
    counts = rows.aggregate(open=Count("id", filter=Q(status__in=OPEN)), ready=Count("id", filter=Q(status=Status.READY)),
                            late=Count("id", filter=Q(status__in=OPEN, promised_on__lt=today) & ~Q(status=Status.READY)))
    if show == "ready":
        rows = rows.filter(status=Status.READY)
    elif show == "late":
        rows = rows.filter(status__in=OPEN, promised_on__lt=today).exclude(status=Status.READY)
    elif show == "delivered":
        rows = rows.filter(status=Status.DELIVERED)
    elif show != "all":
        show = "open"
        rows = rows.filter(status__in=OPEN)
    query = (request.GET.get("q") or "").strip()
    if query:
        rows = rows.filter(Q(number__icontains=query) | Q(customer__name__icontains=query) | Q(customer__phone__icontains=query)
                           | Q(serial__icontains=query) | Q(device__icontains=query))
    rows = list(rows[:200])
    for row in rows:
        row.is_late = row.is_open and row.status != Status.READY and row.promised_on and row.promised_on < today
    return render(request, "workorders/board.html", _base(request, lang, rows=rows, show=show, query=query, counts=counts))


def new(request):
    _guard(request)
    if not services.can_manage(request.user):
        raise PermissionDenied("Receiving needs sales.create_sales_invoice.")
    lang = _lang(request)
    error = ""
    source = request.POST if request.method == "POST" else request.GET
    warranty_of = services.scoped(WorkOrder.objects).filter(pk=source.get("warranty_of") or 0, status=Status.DELIVERED).first()
    if request.method == "POST":
        data = {key: request.POST.get(key) for key in request.POST}
        data["customer"] = Customer.objects.filter(pk=request.POST.get("customer") or 0, active=True).first()
        data["technician"] = _technicians().filter(pk=request.POST.get("technician") or 0).first()
        data["warranty_of"] = warranty_of
        try:
            order = services.receive(data, request.user, lang)
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, WORDS[lang]["received_ok"].format(number=order.number))
            return redirect(f"{reverse('workorders:receipt', args=[order.pk])}?lang={lang}")
    customers = Customer.objects.filter(active=True).exclude(customer_code="WALK-IN").order_by("name")[:500]
    from permissions.services import user_has_permission

    return render(request, "workorders/new.html", _base(request, lang, customers=customers, technicians=_technicians(), post=request.POST,
                                                        error=error, warranty_of=warranty_of,
                                                        can_add_customer=user_has_permission(request.user, "master_data.manage_parties")))


def detail(request, pk):
    _guard(request)
    services.sync_cancelled(request.user)  # a delivery whose invoice was cancelled is open again
    lang = _lang(request)
    order = get_object_or_404(services.visible_to(request.user).select_related("customer", "technician", "invoice", "warranty_of"), pk=pk)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "move":
                to = request.POST.get("to") or ""
                services.move(order, to, request.user, note=request.POST.get("note", ""), lang=lang)
                message = words["moved"].format(status=services.STATUS_LABELS[lang].get(to, to))
            elif action == "details":
                data = {key: request.POST.get(key) for key in request.POST}
                if "technician" in request.POST:
                    data["technician"] = _technicians().filter(pk=request.POST.get("technician") or 0).first()
                services.update_details(order, data, request.user, lang)
                message = words["saved"]
            elif action == "add_line":
                item = Item.objects.filter(pk=request.POST.get("item") or 0, active=True).first()
                services.add_line(order, item, request.user, quantity=request.POST.get("quantity"), unit_price=request.POST.get("unit_price"),
                                  description=request.POST.get("description", ""), is_warranty=request.POST.get("is_warranty") == "1", lang=lang)
                message = words["saved"]
            elif action == "remove_line":
                line = get_object_or_404(WorkOrderLine, pk=request.POST.get("line") or 0, order=order)
                services.remove_line(line, request.user, lang)
                message = words["saved"]
            elif action == "deliver":
                from cashboxes.models import Cashbox
                from entities import scope as entity_scope

                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
                services.deliver(order, request.user, paid_now=request.POST.get("paid_now") or "0", cashbox=cashbox, lang=lang)
                message = words["delivered_ok"].format(number=order.number)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            messages.success(request, message)
            return _back(lang, order)
        order.refresh_from_db()
    from cashboxes.models import Cashbox
    from entities import scope as entity_scope

    numbers = services.totals(order)
    reserved = services.reserved_parts()
    next_moves = [(status, services.STATUS_LABELS[lang][status]) for status in services.NEXT.get(order.status, ())
                  if services.can_manage(request.user) or status in services.TECHNICIAN_MOVES]
    is_own = services.technician_of(request.user) is not None and order.technician_id == services.technician_of(request.user).pk
    items = Item.objects.filter(active=True).order_by("-is_stock_tracked", "item_name")[:500] if services.can_manage(request.user) else []
    today = timezone.localdate()
    return render(request, "workorders/detail.html", _base(
        request, lang, order=order, numbers=numbers, next_moves=next_moves, items=items, error=error, today=today,
        events=order.events.select_related("user"), reserved={line.pk: reserved.get(line.item_id) for line in numbers["lines"]},
        cashboxes=entity_scope.cashboxes(Cashbox.objects).filter(active=True), technicians=_technicians(),
        can_work=services.can_manage(request.user) or is_own,
        under_warranty=order.status == Status.DELIVERED and order.warranty_until and order.warranty_until >= today,
        returns=order.warranty_returns.all()))


def receipt(request, pk):
    from printing.company import company_details

    _guard(request)
    lang = _lang(request)
    order = get_object_or_404(services.visible_to(request.user).select_related("customer", "technician"), pk=pk)
    return render(request, "workorders/receipt.html", {**_base(request, lang, order=order), "company": company_details()})


def report(request):
    import calendar
    from datetime import date

    _guard(request)
    if not services.can_view(request.user):
        raise PermissionDenied("The technicians' report needs sales.view_sales_invoices.")
    services.sync_cancelled(request.user)  # a delivery whose invoice was cancelled is open again
    lang = _lang(request)
    today = timezone.localdate()
    raw = request.GET.get("month") or ""
    try:
        first = date(int(raw[:4]), int(raw[5:7]), 1) if len(raw) == 7 else today.replace(day=1)
    except ValueError:
        first = today.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    rows = services.technician_report(first, last)
    return render(request, "workorders/report.html", _base(request, lang, rows=rows, first=first, today=today,
                                                           late=services.late(today).select_related("customer", "technician").order_by("promised_on")[:50]))
