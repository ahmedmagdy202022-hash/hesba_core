"""T14 screen: couriers, parcels out with them, delivered parcels to settle, and settlements."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from entities import scope as entity_scope
from permissions.decorators import require_permission
from sales.models import SalesInvoice

from . import services
from .models import Courier, Settlement, Shipment, ShipmentStatus

WORDS = {
    "ar": {
        "title": "الشحن والتحصيل عند الاستلام", "intro": "شركة الشحن بتحصّل من العميل وتسلّمك بعدين بعد ما تخصم مصاريفها. لحد ما تسلّم، الفلوس دي مستحقة عليها مش في خزنتك.",
        "couriers": "شركات الشحن", "new_courier": "شركة شحن جديدة", "name": "الاسم", "phone": "التليفون", "fee": "مصاريف الشحنة", "held": "معاها دلوقتي", "out": "مع المندوب (لسه)",
        "save": "حفظ", "saved": "اتحفظ.", "ship": "ابعت مع شركة شحن", "invoice": "الفاتورة", "courier": "شركة الشحن", "tracking": "رقم البوليصة", "cod": "المطلوب تحصيله",
        "cod_hint": "فاضي = الباقي على الفاتورة", "date": "التاريخ", "open": "شحنات مع المندوب", "deliver": "اتسلّمت واتحصّلت", "back": "رجعت", "lost": "ضاعت",
        "reason": "السبب", "to_settle": "اتسلّمت ولسه شركة الشحن ماسلّمتش الفلوس", "settle": "سوّي المختار", "fees": "المصاريف (فاضي = مصاريف الشحنات)",
        "destination": "هتستلم في", "settlements": "التسويات", "collected": "اتحصّل", "net": "الصافي", "none": "مفيش.", "undo": "لغي التسليم",
        "return_hint": "اعمل مرتجع للفاتورة عشان البضاعة ترجع المخزن.", "status": "الحالة", "customer": "العميل",
    },
    "en": {
        "title": "Shipping & cash on delivery", "intro": "The courier collects from the customer and hands it over later, less its fees. Until then the money is owed by the courier, not in your cashbox.",
        "couriers": "Couriers", "new_courier": "New courier", "name": "Name", "phone": "Phone", "fee": "Fee per parcel", "held": "Holds now", "out": "Out for delivery",
        "save": "Save", "saved": "Saved.", "ship": "Ship with a courier", "invoice": "Invoice", "courier": "Courier", "tracking": "Tracking number", "cod": "To collect",
        "cod_hint": "Empty = what is left on the invoice", "date": "Date", "open": "With the courier", "deliver": "Delivered and collected", "back": "Came back", "lost": "Lost",
        "reason": "Reason", "to_settle": "Delivered, not handed over yet", "settle": "Settle the chosen", "fees": "Fees (empty = the parcels' fees)",
        "destination": "Receive into", "settlements": "Settlements", "collected": "Collected", "net": "Net", "none": "None.", "undo": "Undo delivery",
        "return_hint": "Make a return for the invoice so the goods come back to stock.", "status": "Status", "customer": "Customer",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc):
    return " ".join(getattr(exc, "messages", [str(exc)]))


@require_permission("sales.view_sales_invoices")
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "courier":
                courier = get_object_or_404(Courier, pk=request.POST["courier"]) if request.POST.get("courier", "").isdigit() else None
                services.save_courier(request.POST, request.user, courier, lang)
            elif action == "ship":
                invoice = get_object_or_404(entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE), pk=request.POST.get("invoice") or 0)
                courier = get_object_or_404(Courier, pk=request.POST.get("courier") or 0)
                services.ship(invoice, courier, request.user, request.POST, lang)
            elif action in ("deliver", "back", "lost", "undo"):
                shipment = get_object_or_404(Shipment, pk=request.POST.get("shipment") or 0)
                if action == "deliver":
                    services.deliver(shipment, request.user, request.POST.get("day"), lang)
                elif action == "back":
                    services.came_back(shipment, request.user, request.POST.get("reason"), request.POST.get("day"), lang)
                elif action == "lost":
                    services.lose(shipment, request.user, request.POST.get("reason"), request.POST.get("day"), lang)
                else:
                    services.undo_delivery(shipment, request.user, request.POST.get("reason"), lang)
            elif action == "settle":
                destination = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("destination") or 0).first()
                services.settle(request.POST.getlist("shipments"), destination, request.user, request.POST, lang)
            else:
                raise ValidationError(services.MESSAGES[lang]["number"])
        except (ValidationError, PermissionDenied) as exc:
            flash.error(request, _error(exc))
        else:
            flash.success(request, words["saved"])
        return redirect(f"{reverse('shipping:board')}?lang={lang}")
    invoice = None
    if request.GET.get("invoice", "").isdigit():
        invoice = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(pk=request.GET["invoice"], status="posted").select_related("customer").first()
    delivered = Shipment.objects.filter(status=ShipmentStatus.DELIVERED, settlement__isnull=True).select_related("invoice__customer", "courier")
    return render(request, "shipping/board.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "invoice": invoice, "today": timezone.localdate(),
        "couriers": services.courier_rows(), "active_couriers": Courier.objects.filter(active=True),
        "open": Shipment.objects.filter(status=ShipmentStatus.WITH_COURIER).select_related("invoice__customer", "courier"),
        "delivered": delivered, "settlements": Settlement.objects.select_related("courier", "destination")[:20],
        "destinations": entity_scope.cashboxes(Cashbox.objects).filter(active=True), "can_ship": services.can_ship(request.user),
        "closed": Shipment.objects.filter(status__in=(ShipmentStatus.RETURNED, ShipmentStatus.LOST)).select_related("invoice", "courier")[:20],
    })
