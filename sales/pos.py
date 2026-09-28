"""POS-001: the cashier screen. Scan, total, take the money, print the receipt.

It is a front end over the existing sales services. A POS sale is created with
``create_sales_draft`` and posted with ``post_sales_invoice`` inside one
transaction, so stock, cashbox and customer-ledger effects are exactly those of
any posted invoice. If posting fails (for example not enough stock), nothing is
kept.
"""

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from barcode.services import item_catalog
from pricing.services import price_book
from taxes.services import compute_lines, create_sales_draft_with_tax, rates_by_item, vat_enabled
from serials.services import attach_sale_serials, pos_serial_catalog, prepare_sale_serials
from shifts.services import open_shift_for
from .pos_customers import customer_directory
from units.services import units_catalog
from cashboxes.models import Cashbox
from config.money import money_round
from master_data.models import Customer, Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core.module_gate import closed_module

from .models import SalesInvoice
from .services import create_sales_draft, post_sales_invoice


WALK_IN_CODE = "WALK-IN"
MAX_LINES = 200

WORDS = {
    "ar": {
        "page_title": "الكاشير",
        "title": "الكاشير",
        "scan": "امسح الباركود أو اكتب الكود",
        "scan_hint": "Enter للإضافة · F9 للدفع",
        "search": "أو دوّر بالاسم",
        "customer": "العميل",
        "location": "المخزن",
        "cashbox": "الخزنة",
        "item": "الصنف",
        "qty": "الكمية",
        "price": "السعر",
        "total": "الإجمالي",
        "remove": "شيل",
        "empty_cart": "السلة فاضية. امسح أول صنف.",
        "subtotal": "المجموع",
        "discount": "خصم",
        "grand_total": "المطلوب",
        "tendered": "المدفوع",
        "change": "الباقي للعميل",
        "credit": "آجل على العميل",
        "pay_print": "دفع وطباعة الإيصال",
        "pay_only": "دفع بدون طباعة",
        "clear": "سلة جديدة",
        "today": "مبيعاتك النهارده",
        "count": "عدد الفواتير",
        "cash_in": "نقدي",
        "credit_total": "آجل",
        "sold_total": "إجمالي البيع",
        "done": "تم البيع: فاتورة {number} بمبلغ {total}.",
        "change_msg": "الباقي للعميل {change}.",
        "receipt": "طباعة الإيصال",
        "walk_in": "عميل نقدي",
        "err_empty": "السلة فاضية.",
        "err_line": "في سطر فيه كمية أو سعر مش صحيح.",
        "err_item": "صنف مش موجود أو موقوف.",
        "err_walk_in_credit": "البيع الآجل محتاج عميل باسمه؛ اختار العميل أو خد المبلغ كامل.",
        "err_discount": "الخصم أكبر من المجموع.",
        "err_setup": "لازم يكون فيه مخزن بيع وخزنة نشطين قبل البيع.",
        "scan_serial": "الصنف ده بيتباع بالسيريال؛ امسح السيريال / IMEI بتاع القطعة: ",
        "serial_twice": "السيريال ده في السلة بالفعل: ",
        "not_found": "مفيش صنف بالكود ده: ",
        "added": "اتضاف: ",
        "back": "فواتير البيع",
    },
    "en": {
        "page_title": "Point of sale",
        "title": "Point of sale",
        "scan": "Scan a barcode or type a code",
        "scan_hint": "Enter to add · F9 to pay",
        "search": "or search by name",
        "customer": "Customer",
        "location": "Location",
        "cashbox": "Cashbox",
        "item": "Item",
        "qty": "Qty",
        "price": "Price",
        "total": "Total",
        "remove": "Remove",
        "empty_cart": "The cart is empty. Scan the first item.",
        "subtotal": "Subtotal",
        "discount": "Discount",
        "grand_total": "To pay",
        "tendered": "Paid",
        "change": "Change",
        "credit": "On the customer's account",
        "pay_print": "Pay and print receipt",
        "pay_only": "Pay without printing",
        "clear": "New cart",
        "today": "Your sales today",
        "count": "Invoices",
        "cash_in": "Cash",
        "credit_total": "Credit",
        "sold_total": "Sold",
        "done": "Sold: invoice {number} for {total}.",
        "change_msg": "Change due {change}.",
        "receipt": "Print receipt",
        "walk_in": "Walk-in customer",
        "err_empty": "The cart is empty.",
        "err_line": "A line has an invalid quantity or price.",
        "err_item": "An item is missing or inactive.",
        "err_walk_in_credit": "A credit sale needs a named customer; pick the customer or take the full amount.",
        "err_discount": "The discount is larger than the subtotal.",
        "err_setup": "A selling location and an active cashbox are needed before selling.",
        "scan_serial": "This item is sold by serial; scan the unit's serial / IMEI: ",
        "serial_twice": "That serial is already in the cart: ",
        "not_found": "No item with this code: ",
        "added": "Added: ",
        "back": "Sales invoices",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def walk_in_customer():
    customer, _ = Customer.objects.get_or_create(
        customer_code=WALK_IN_CODE,
        defaults={"name": "عميل نقدي", "notes": "Created by the cashier screen for sales without a named customer."},
    )
    return customer


def _decimal(value, places):
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(Decimal(1).scaleb(-places))


def parse_cart(post):
    """The cart lines from the posted form, validated. Raises ValidationError."""

    try:
        count = min(int(post.get("line_count", "0")), MAX_LINES)
    except ValueError:
        count = 0
    rows = []
    for index in range(count):
        item_id = post.get(f"item_{index}", "")
        if not item_id:
            continue
        quantity = _decimal(post.get(f"qty_{index}"), 3)
        price = _decimal(post.get(f"price_{index}"), 2)
        if quantity is None or price is None or quantity <= 0 or price < 0:
            raise ValidationError("err_line")
        rows.append((item_id, quantity, price, post.get(f"unit_{index}", ""), post.get(f"serial_{index}", "")))
    if not rows:
        raise ValidationError("err_empty")
    items = {str(item.pk): item for item in Item.objects.filter(pk__in=[row[0] for row in rows if row[0].isdigit()], active=True)}
    from units.models import ItemUnit
    from units.services import convert_line, units_enabled

    unit_ids = [row[3] for row in rows if row[3].isdigit()]
    units = {str(unit.pk): unit for unit in ItemUnit.objects.filter(pk__in=unit_ids, active=True)} if unit_ids and units_enabled() else {}
    lines = []
    for item_id, quantity, price, unit_id, serial in rows:
        item = items.get(item_id)
        if item is None:
            raise ValidationError("err_item")
        line = {"item": item, "quantity": quantity, "unit_sale_price": price, "line_discount_amount": Decimal("0"), "description": ""}
        unit = units.get(unit_id)
        if unit_id and unit is None and units_enabled():
            raise ValidationError("err_item")
        if unit is not None:
            if unit.item_id != item.pk:
                raise ValidationError("err_item")
            line = convert_line(dict(line, unit=unit), "unit_sale_price")  # UNITS-001: stored in base units
        if serial:
            line["serials"] = serial  # SERIAL-001: checked against stock by prepare_sale_serials
        lines.append(line)
    return lines


def _next_number(today):
    prefix = f"POS-{today:%Y%m%d}-"
    last = SalesInvoice.objects.filter(invoice_number__startswith=prefix).order_by("-invoice_number").values_list("invoice_number", flat=True).first()
    sequence = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{sequence:04d}"


def checkout(*, lines, customer, location, cashbox, discount, tendered, user, sale_date=None, notes="POS"):
    """Create and post the sale. Returns (invoice, change).

    POS-003: an offline sale passes the day it was rung up as ``sale_date``;
    posting then applies that day's period rules like any other entry.
    """

    # UNITS-001: a line entered in a bigger unit carries a few piastres of discount.
    subtotal = money_round(sum((money_round(line["quantity"] * line["unit_sale_price"] - (line.get("line_discount_amount") or 0)) for line in lines), Decimal("0")))
    discount = money_round(discount or 0)
    if discount < 0 or discount > subtotal:
        raise ValidationError("err_discount")
    # TAX-001: VAT per line from item rates; the invoice discount comes after tax.
    tax = money_round(sum((line_tax for *_, line_tax in compute_lines(lines)), Decimal("0"))) if vat_enabled() else Decimal("0.00")
    total = money_round(subtotal - discount + tax)
    tendered = money_round(tendered or 0)
    paid_now = min(tendered, total)
    if paid_now < total and customer.customer_code == WALK_IN_CODE:
        raise ValidationError("err_walk_in_credit")
    change = money_round(tendered - total) if tendered > total else Decimal("0.00")
    today = sale_date or timezone.localdate()
    for _attempt in range(5):
        try:
            with transaction.atomic():
                invoice = create_sales_draft_with_tax(
                    {
                        "invoice_number": _next_number(today),
                        "invoice_date": today,
                        "customer": customer,
                        "selling_location": location,
                        "cashbox": cashbox,
                        "discount_amount": discount,
                        "tax_amount": Decimal("0"),
                        "paid_now": paid_now,
                        "notes": notes,
                    },
                    lines,
                    user,
                )
                attach_sale_serials(invoice, lines)  # SERIAL-001
                post_sales_invoice(invoice.pk, user)
            invoice.refresh_from_db()
            return invoice, change
        except IntegrityError:
            continue  # another till took the same number a moment ago
    raise ValidationError("Could not allocate an invoice number; try again.")


def today_summary(user):
    today = timezone.localdate()
    rows = SalesInvoice.objects.filter(invoice_date=today, status="posted", created_by=user)
    totals = rows.aggregate(count=Count("id"), total=Sum("total_amount"), cash=Sum("paid_now"), credit=Sum("remaining_due"))
    return {key: value or 0 for key, value in totals.items()}


def _clean_cart(raw):
    """The cart to put back on the screen after a refused sale: ids and numbers only."""

    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        return []
    clean = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id", ""))
        unit_id = str(row.get("unit", "") or "")
        qty, price = _decimal(row.get("qty", ""), 3), _decimal(row.get("price", ""), 2)
        if item_id.isdigit() and qty is not None and price is not None:
            row_back = {"id": int(item_id), "qty": str(qty), "price": str(price)}
            if unit_id.isdigit():
                row_back["unit"] = int(unit_id)
            serial = str(row.get("serial", "") or "")[:80]
            if serial:
                row_back["serial"] = serial
            clean.append(row_back)
    return clean[:MAX_LINES]


def _error_text(exc, words):
    parts = []
    for message in getattr(exc, "messages", [str(exc)]):
        parts.append(words.get(message, message))
    return " ".join(parts)


@require_permission("sales.create_sales_invoice")
def pos(request):
    lang = _lang(request)
    words = WORDS[lang]
    locations = Location.objects.filter(active=True, is_selling_location=True)
    cashboxes = Cashbox.objects.filter(active=True)
    customer_default = walk_in_customer()
    cart_back = "[]"  # raw JSON from the form; cleaned before it is rendered
    selected = {
        "customer": str(customer_default.pk),
        "location": str((locations.filter(is_default=True).first() or locations.first() or Location()).pk or ""),
        "cashbox": str((cashboxes.filter(is_default=True).first() or cashboxes.first() or Cashbox()).pk or ""),
    }
    if request.method == "POST":
        selected = {key: request.POST.get(key, "") for key in selected}
        try:
            lines = prepare_sale_serials(parse_cart(request.POST), lang)
            location = locations.filter(pk=selected["location"]).first() if selected["location"].isdigit() else None
            cashbox = cashboxes.filter(pk=selected["cashbox"]).first() if selected["cashbox"].isdigit() else None
            if location is None or cashbox is None:
                raise ValidationError("err_setup")
            customer = Customer.objects.filter(pk=selected["customer"], active=True).first() if selected["customer"].isdigit() else None
            customer = customer or customer_default
            discount = _decimal(request.POST.get("discount") or "0", 2)
            tendered = _decimal(request.POST.get("tendered") or "0", 2)
            if discount is None or tendered is None or tendered < 0:
                raise ValidationError("err_line")
            invoice, change = checkout(lines=lines, customer=customer, location=location, cashbox=cashbox, discount=discount, tendered=tendered, user=request.user)
        except ValidationError as exc:
            messages.error(request, _error_text(exc, words))
            cart_back = request.POST.get("cart_json", "[]")
        else:
            text = words["done"].format(number=invoice.invoice_number, total=f"{invoice.total_amount:,.2f}")
            if change:
                text += " " + words["change_msg"].format(change=f"{change:,.2f}")
            messages.success(request, text)
            if request.POST.get("print") == "1" and closed_module("/print/") is None:
                return redirect(f"/print/sales/{invoice.pk}/?lang={lang}&format=receipt&autoprint=1&next=pos")
            return redirect(f"/sales/pos/?lang={lang}&last={invoice.pk}")
    cart_back = _clean_cart(cart_back)
    last = request.GET.get("last", "")
    return render(
        request,
        "sales/pos.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "page_title": words["page_title"],
            "section": "sales",
            "catalog": item_catalog(sale_prices=True),
            "price_book": price_book(),
            "tax_rates": rates_by_item(),
            "units": units_catalog(),
            "open_shift": open_shift_for(request.user),
            "serial_catalog": pos_serial_catalog(),
            "customers": Customer.objects.filter(active=True).order_by("name"),
            "customer_directory": customer_directory(),  # POS-002
            "can_add_customer": user_has_permission(request.user, "master_data.manage_parties"),
            "walk_in": customer_default,
            "locations": locations,
            "cashboxes": cashboxes,
            "selected": selected,
            "cart_back": cart_back,
            "summary": today_summary(request.user),
            "last_invoice": SalesInvoice.objects.filter(pk=last, created_by=request.user).first() if last.isdigit() else None,
            "js_words": json.dumps({key: words[key] for key in ("not_found", "added", "remove", "empty_cart", "scan_serial", "serial_twice")}, ensure_ascii=False),
        },
    )
