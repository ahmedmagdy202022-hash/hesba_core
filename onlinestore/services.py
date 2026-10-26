"""T13 (MARKET-PARITY): an online store by file, until a store account is linked.

* ``export_csv(kind)``: the catalogue as the store's own product CSV, imported there as is: Shopify's product CSV or
  WooCommerce's product importer. The SKU is the item code; a size/colour group (VARIANT-001) is one product with
  its options; the stock is what the selling locations of the entity being worked in hold now, in whole units; the
  price is the item's sale price.
* ``import_orders(raw, location, user)``: a Shopify orders export, or the Hesba template (what a WooCommerce order
  export plugin can be set to write), becomes draft sales invoices, one per order, its lines matched by SKU (item
  code, then barcode). The customer is found by phone, or added. Nothing is posted: each draft is checked and posted
  as usual, so stock, cash and the ledger move only through the ordinary sales posting. An order is imported once.
  Codex #273: the store's shipping charge can go on a line of a chosen service item, prices that include VAT can be
  taken back to before tax, and an order whose draft was not posted yet can be uploaded again to replace that draft —
  the way to correct an order, since a draft has no edit screen. A serial-tracked item needs its serials in the file.
"""

import csv
import io
import re
from collections import OrderedDict, defaultdict
from datetime import date
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from config.money import money_round
from permissions.services import user_has_permission

from .models import StoreOrder, StoreSource

ZERO = Decimal("0")
HUNDRED = Decimal("100")
EXPORT = "master_data.view_master_data"
IMPORT = "sales.create_sales_invoice"
ADD_CUSTOMER = "master_data.manage_parties"
MAX_IMPORT_BYTES = 2 * 1024 * 1024
KINDS = ("shopify", "woocommerce")

#: Shopify's product CSV, in its order (help.shopify.com, "Using CSV files to import and export products").
SHOPIFY_COLUMNS = (
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published", "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value",
    "Option3 Name", "Option3 Value", "Variant SKU", "Variant Grams", "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price", "Variant Compare-at Price", "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Alt Text",
)
#: The WooCommerce product importer's own column names (its "Product CSV Import Schema"); the rest are left out.
WOO_COLUMNS = (
    "Type", "SKU", "Name", "Published", "Short description", "Description", "Tax status", "In stock?", "Stock", "Regular price", "Categories", "Parent",
    "Attribute 1 name", "Attribute 1 value(s)", "Attribute 1 visible", "Attribute 1 global",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 2 visible", "Attribute 2 global",
)
#: The Hesba orders template: English or Arabic headings.
TEMPLATE_COLUMNS = ("order", "date", "customer", "phone", "email", "address", "sku", "quantity", "price", "discount", "total")
TEMPLATE_ALIASES = {
    "order": ("order", "order number", "رقم الطلب"), "date": ("date", "order date", "التاريخ"), "customer": ("customer", "name", "العميل"),
    "phone": ("phone", "التليفون", "الموبايل"), "email": ("email", "الإيميل"), "address": ("address", "العنوان"),
    "sku": ("sku", "code", "الكود"), "quantity": ("quantity", "qty", "الكمية"), "price": ("price", "unit price", "السعر"),
    "discount": ("discount", "الخصم"), "total": ("total", "الإجمالي"), "serial": ("serial", "serials", "السيريال"),
    "currency": ("currency", "العملة"),
}

MESSAGES = {
    "ar": {
        "file": "اختار ملف الطلبات.", "big": "الملف أكبر من 2 ميجا؛ قسّمه.", "format": "الملف مش ملف طلبات Shopify ولا قالب حسبة: لازم أعمدة رقم الطلب والكود والكمية والسعر.",
        "location": "اختار المخزن اللي الطلبات هتطلع منه.", "sku": "الكود {sku} مش موجود في الأصناف.", "number": "سطر فيه كمية أو سعر مش مفهوم.",
        "refunded": "الطلب اترد أو اتلغى في المتجر ({status}).", "taken": "رقم الفاتورة {number} مستخدم قبل كده.", "empty": "الطلب من غير أصناف.",
        "notes": "طلب من المتجر الأونلاين {source} رقم {number}.", "shipping": "مصاريف شحن اتحصّلت في المتجر: {amount}.", "who": "العميل: {who}.",
        "customer_note": "اتضاف من طلب المتجر الأونلاين.", "shipping_line": "مصاريف الشحن", "posted": "الطلب ده فاتورته اترحّلت؛ مينفعش يتبدّل.",
        "currency": "الطلب بعملة {currency} وحسبة شغالة بـ {own}؛ حوّله الأول أو سجّله يدوي.",
        "replaced": "اتبدّلت مسودته.", "elsewhere": "رقم الطلب ده متسجّل لكيان تاني؛ مش هيتبدّل من هنا.",
    },
    "en": {
        "file": "Choose the orders file.", "big": "The file is over 2 MB; split it.", "format": "This is neither a Shopify orders export nor the Hesba template: it needs order, SKU, quantity and price columns.",
        "location": "Choose the location the orders leave from.", "sku": "SKU {sku} is not an item code or barcode.", "number": "A line has a quantity or price that is not understood.",
        "refunded": "The order was refunded or voided in the store ({status}).", "taken": "Invoice number {number} is taken already.", "empty": "The order has no lines.",
        "notes": "Online store order, {source} number {number}.", "shipping": "Shipping charged in the store: {amount}.", "who": "Customer: {who}.",
        "customer_note": "Added from an online store order.", "shipping_line": "Shipping", "posted": "This order's invoice is posted; it cannot be replaced.",
        "currency": "The order is in {currency} and Hesba works in {own}; convert it first or enter it by hand.",
        "replaced": "its draft was replaced.", "elsewhere": "This order number belongs to another entity; it is not replaced from here.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def can_export(user):
    return user_has_permission(user, EXPORT)


def can_import(user):
    return user_has_permission(user, IMPORT)


# ---- the catalogue out ----

def _handle(code, pk):
    text = re.sub(r"[^a-z0-9]+", "-", str(code).lower()).strip("-")
    return text or f"item-{pk}"


def _handles(catalogue):
    """{(kind, pk): handle} for every product, unique across the catalogue (Codex #273): codes that read the same once
    cleaned (AB-1, AB_1), or a group and an item with the same code, get their own kind and id added."""

    keys = [("group", group.pk, group.code) if group else ("item", items[0].pk, items[0].item_code) for group, items in catalogue]
    base = {(kind, pk): _handle(code, pk) for kind, pk, code in keys}
    seen = {}
    for handle in base.values():
        seen[handle] = seen.get(handle, 0) + 1
    return {key: handle if seen[handle] == 1 else f"{handle}-{key[0][0]}{key[1]}" for key, handle in base.items()}


def _money(value):
    return f"{money_round(value or ZERO):.2f}"


def _stock():
    """{item_id: whole units} held now by the selling locations of the entity being worked in."""

    from entities import scope as entity_scope
    from inventory.warehouses import on_hand
    from master_data.models import Location

    locations = entity_scope.locations(Location.objects.filter(active=True, is_selling_location=True))
    totals = defaultdict(lambda: ZERO)
    for (_location, item), quantity in on_hand(locations).items():
        totals[item] += quantity
    return {item: max(0, int(quantity)) for item, quantity in totals.items()}


def _catalogue():
    """[(group or None, [items])]: a size/colour group's items together, every other item on its own."""

    from master_data.models import Item
    from variants.models import Variant

    items = list(Item.objects.filter(active=True).select_related("category").order_by("item_code"))
    variant_of = {row.item_id: row for row in Variant.objects.select_related("group").filter(item__in=items, group__active=True)}
    out, groups = [], OrderedDict()
    for item in items:
        variant = variant_of.get(item.pk)
        if variant is None:
            out.append((None, [item]))
        else:
            groups.setdefault(variant.group.pk, (variant.group, []))[1].append(item)
    out.extend(groups.values())
    return out, variant_of


def _parent_skus(catalogue):
    """{group id: SKU} for each size/colour group's WooCommerce parent (Codex #273). WooCommerce matches products by SKU,
    and a group's code may equal an item code, so such a parent gets a SKU of its own; the items keep their codes."""

    from master_data.models import Item

    taken = set(Item.objects.values_list("item_code", flat=True))
    out = {}
    for group, _items in catalogue:
        if group is None:
            continue
        sku = group.code if group.code not in taken else f"{group.code}-group-{group.pk}"
        while sku in taken:
            sku += "-x"
        taken.add(sku)
        out[group.pk] = sku
    return out


def _options(group, items, variant_of):
    """The option names a group uses (size, colour), each with its values in the group's order."""

    names = []
    if any(variant_of[item.pk].size for item in items):
        names.append(("Size", "size"))
    if any(variant_of[item.pk].color for item in items):
        names.append(("Color", "color"))
    return names


def _taxed():
    from taxes.services import rates_by_item

    rates = rates_by_item()
    return (lambda item: False) if rates is None else (lambda item: Decimal(rates.get(str(item.pk), "0")) > 0)


def export_rows(kind, user):
    """The rows of the product CSV for ``kind`` (shopify or woocommerce), headings first."""

    if not can_export(user):
        raise PermissionDenied(f"The store export needs {EXPORT}.")
    if kind not in KINDS:
        raise ValidationError("Unknown store.")
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    vendor = (profile.display_name if profile is not None else "") or "Hesba"
    stock, taxed = _stock(), _taxed()
    catalogue, variant_of = _catalogue()

    def category(item_or_group):
        found = getattr(item_or_group, "category", None)
        return (found.name_en or found.name_ar) if found is not None else ""

    def quantity(item):
        return str(stock.get(item.pk, 0)) if item.is_stock_tracked else ""

    rows = [list(SHOPIFY_COLUMNS if kind == "shopify" else WOO_COLUMNS)]
    handles = _handles(catalogue)
    parent_skus = _parent_skus(catalogue)
    for group, items in catalogue:
        if kind == "shopify":
            options = _options(group, items, variant_of) if group else []
            for index, item in enumerate(items):
                first = index == 0
                row = dict.fromkeys(SHOPIFY_COLUMNS, "")
                row.update({
                    "Handle": handles[("group", group.pk) if group else ("item", items[0].pk)], "Variant SKU": item.item_code, "Variant Grams": "0",
                    "Variant Inventory Tracker": "shopify" if item.is_stock_tracked else "", "Variant Inventory Qty": quantity(item),
                    "Variant Inventory Policy": "deny", "Variant Fulfillment Service": "manual", "Variant Price": _money(item.default_sale_price),
                    "Variant Requires Shipping": "TRUE", "Variant Taxable": "TRUE" if taxed(item) else "FALSE", "Variant Barcode": item.barcode,
                })
                if first:
                    row.update({"Title": group.name if group else item.item_name, "Vendor": vendor, "Type": category(group or item), "Published": "TRUE"})
                if not options:
                    row.update({"Option1 Name": "Title" if first else "", "Option1 Value": "Default Title"})
                for number, (name, field) in enumerate(options, start=1):
                    row[f"Option{number} Value"] = getattr(variant_of[item.pk], field) or "-"
                    if first:
                        row[f"Option{number} Name"] = name
                rows.append([row[column] for column in SHOPIFY_COLUMNS])
        else:
            def woo(**values):
                row = dict.fromkeys(WOO_COLUMNS, "")
                row.update(values)
                rows.append([row[column] for column in WOO_COLUMNS])

            def stock_cells(item):
                held = quantity(item)
                return {"Stock": held, "In stock?": "1" if not item.is_stock_tracked or int(held or 0) > 0 else "0", "Regular price": _money(item.default_sale_price),
                        "Tax status": "taxable" if taxed(item) else "none"}

            if group is None:
                item = items[0]
                woo(Type="simple", SKU=item.item_code, Name=item.item_name, Published="1", Categories=category(item), **stock_cells(item))
                continue
            options = _options(group, items, variant_of)
            parent_sku = parent_skus[group.pk]
            parent = {"Type": "variable", "SKU": parent_sku, "Name": group.name, "Published": "1", "Categories": category(group),
                      "Tax status": "taxable" if taxed(items[0]) else "none"}
            for number, (name, field) in enumerate(options, start=1):
                values = list(OrderedDict.fromkeys(getattr(variant_of[item.pk], field) or "-" for item in items))
                parent.update({f"Attribute {number} name": name, f"Attribute {number} value(s)": ", ".join(values), f"Attribute {number} visible": "1",
                               f"Attribute {number} global": "0"})
            woo(**parent)
            for item in items:
                variation = {"Type": "variation", "SKU": item.item_code, "Name": item.item_name, "Published": "1", "Parent": parent_sku, **stock_cells(item)}
                for number, (name, field) in enumerate(options, start=1):
                    variation.update({f"Attribute {number} name": name, f"Attribute {number} value(s)": getattr(variant_of[item.pk], field) or "-",
                                      f"Attribute {number} global": "0"})
                woo(**variation)
    return rows


def export_csv(kind, user):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(export_rows(kind, user))
    return buffer.getvalue()  # UTF-8 without a BOM: a BOM would become part of the first heading in the store


def template_csv():
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TEMPLATE_COLUMNS)
    writer.writerow(["1001", timezone.localdate().isoformat(), "سارة أحمد", "01001234567", "", "القاهرة", "IT-001", "2", "150.00", "0", "300.00"])
    return "﻿" + buffer.getvalue()  # filled in a spreadsheet, so Arabic needs the BOM there


# ---- orders in ----

def _key(cell):
    return re.sub(r"\s+", " ", str(cell or "").replace("﻿", "")).strip().lower()


def _read(raw, words):
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValidationError(words["big"])
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise ValidationError(words["format"])
    return [_key(cell) for cell in rows[0]], rows[1:]


def _decimal(text, words, default=None):
    """A number as stores and spreadsheets write it (Codex #273): Arabic digits and separators, 1,234.50 or 12,50; a
    currency or spaces around it are dropped, anything else is refused rather than guessed."""

    text = _latin(text).replace("\u066b", ".").replace("\u066c", "").replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"^[^\d\-.,]+|[^\d.,]+$", "", text)  # "EGP 12.50", "12.50 ج.م"
    if not text:
        if default is None:
            raise ValidationError(words["number"])
        return default
    if "," in text and "." in text:
        text = text.replace(",", "")  # 1,234.50
    elif text.count(",") == 1 and len(text.split(",")[1]) <= 2:
        text = text.replace(",", ".")  # 12,50
    elif "," in text:
        text = text.replace(",", "")  # 1,234 or 1,234,567
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise ValidationError(words["number"])
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValidationError(words["number"])


def _latin(text):
    return "".join(str(int(ch)) if ch.isdecimal() else ch for ch in str(text or ""))


def _day(text):
    try:
        return date.fromisoformat(_latin(text).strip()[:10])
    except ValueError:
        return timezone.localdate()


def _shopify_orders(headers, rows):
    """{number: order} from a Shopify orders export: one row per line, the order's own fields on its first row."""

    at = {name: index for index, name in enumerate(headers)}

    def cell(row, *names):
        for name in names:
            index = at.get(name)
            if index is not None and index < len(row) and row[index].strip():
                return row[index].strip()
        return ""

    orders = OrderedDict()
    for row in rows:
        number = cell(row, "name").lstrip("#")
        if not number:
            continue
        order = orders.setdefault(number, {"number": number, "lines": [], "date": "", "customer": "", "phone": "", "email": "", "address": "",
                                           "discount": "", "shipping": "", "total": "", "status": "", "currency": ""})
        for field, names in (("date", ("created at",)), ("customer", ("billing name", "shipping name")), ("phone", ("billing phone", "phone", "shipping phone")),
                             ("email", ("email",)), ("address", ("shipping address1", "billing address1")), ("discount", ("discount amount",)),
                             ("shipping", ("shipping",)), ("total", ("total",)), ("status", ("financial status",)), ("currency", ("currency",))):
            order[field] = order[field] or cell(row, *names)
        city = cell(row, "shipping city", "billing city")
        if city and city not in order["address"]:
            order["address"] = f"{order['address']}، {city}".strip("، ")
        sku = cell(row, "lineitem sku")
        if sku or cell(row, "lineitem name"):
            order["lines"].append({"sku": sku or cell(row, "lineitem name"), "quantity": cell(row, "lineitem quantity"), "price": cell(row, "lineitem price")})
    return orders


def _template_orders(headers, rows):
    at = {}
    for field, names in TEMPLATE_ALIASES.items():
        at[field] = next((headers.index(name) for name in names if name in headers), None)

    def cell(row, field):
        index = at[field]
        return row[index].strip() if index is not None and index < len(row) else ""

    orders = OrderedDict()
    for row in rows:
        number = cell(row, "order").lstrip("#")
        if not number:
            continue
        order = orders.setdefault(number, {"number": number, "lines": [], "date": "", "customer": "", "phone": "", "email": "", "address": "",
                                           "discount": "", "shipping": "", "total": "", "status": "", "currency": ""})
        for field in ("date", "customer", "phone", "email", "address", "discount", "total", "currency"):
            order[field] = order[field] or cell(row, field)
        order["lines"].append({"sku": cell(row, "sku"), "quantity": cell(row, "quantity"), "price": cell(row, "price"), "serials": cell(row, "serial")})
    return orders


def read_orders(raw, lang="ar"):
    """(source, {number: order}) from an uploaded orders file."""

    words = _words(lang)
    headers, rows = _read(raw, words)
    if {"name", "lineitem sku", "lineitem quantity", "lineitem price"} <= set(headers):
        return StoreSource.SHOPIFY, _shopify_orders(headers, rows)
    needed = ("order", "sku", "quantity", "price")
    if all(any(name in headers for name in TEMPLATE_ALIASES[field]) for field in needed):
        return StoreSource.TEMPLATE, _template_orders(headers, rows)
    raise ValidationError(words["format"])


def _phone_key(text):
    digits = "".join(str(int(ch)) for ch in str(text or "") if ch.isdecimal())
    if digits.startswith("0020"):
        digits = digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = digits[2:]
    return digits.lstrip("0")


def phone_index():
    """{phone digits: customer id} of the active customers, however their phones were typed (spaces, +20, Arabic digits)."""

    from master_data.models import Customer

    index = {}
    for pk, phone in Customer.objects.filter(active=True).exclude(phone="").order_by("pk").values_list("pk", "phone"):
        index.setdefault(_phone_key(phone), pk)
    return index


def _customer(order, user, words, phones):
    """The customer with this phone, else a new one (who may add customers), else the walk-in customer."""

    from config.numbering import next_in_series
    from master_data.models import Customer
    from sales.pos import walk_in_customer

    key = _phone_key(order["phone"])
    if len(key) >= 7 and key in phones:
        return Customer.objects.get(pk=phones[key]), False
    if not order["customer"] or not user_has_permission(user, ADD_CUSTOMER):
        return walk_in_customer(), False
    for _attempt in range(5):
        try:
            with transaction.atomic():
                customer = Customer.objects.create(customer_code=next_in_series(Customer, "customer_code", "C-"), name=order["customer"][:255],
                                                   phone=order["phone"][:50], email=order["email"][:254] if "@" in order["email"] else "",
                                                   address=order["address"], notes=words["customer_note"])
        except IntegrityError:
            continue
        if len(key) >= 7:
            phones[key] = customer.pk  # the same buyer's next order in this file finds them
        return customer, True
    raise ValidationError("Could not allocate a customer code; try again.")


def _item(sku):
    from master_data.models import Item

    sku = str(sku or "").strip()
    if not sku:
        return None
    return Item.objects.filter(active=True, item_code=sku).first() or Item.objects.filter(active=True, barcode=sku).first()


def _spread_discount(lines, gross, rates, discount):
    """Codex #273: a store's discount comes off the product lines before VAT, as the store takes it, not after tax like
    Hesba's invoice discount. Each line takes its share of the store's (possibly VAT-inclusive) amount, and a VAT-
    inclusive share is taken back to before tax at the line's rate."""

    base = sum(gross, ZERO)
    if discount <= 0 or base <= 0:
        return
    discount, left = min(discount, base), min(discount, base)
    for index, line in enumerate(lines):
        share = left if index == len(lines) - 1 else min(money_round(discount * gross[index] / base), left)
        left -= share
        pre_tax = money_round(share * HUNDRED / (HUNDRED + rates[index])) if rates[index] else share
        line["line_discount_amount"] = min(pre_tax, money_round(line["quantity"] * line["unit_sale_price"]))


class _AlreadyImported(Exception):
    """The order was imported by an upload running at the same time."""


def _draft(source, order, location, user, words, phones, options):
    from config.numbering import next_in_series
    from sales.models import SalesInvoice
    from sales.pos import WALK_IN_CODE
    from serials.services import attach_sale_serials, prepare_sale_serials
    from taxes.services import create_sales_draft_with_tax

    if order["status"].lower() in ("refunded", "voided", "partially_refunded"):
        raise ValidationError(words["refunded"].format(status=order["status"]))
    if not order["lines"]:
        raise ValidationError(words["empty"])
    own = (options.get("currency") or "EGP").upper()
    if order.get("currency") and order["currency"].strip().upper() != own:  # Codex #273: one company currency, no conversion
        raise ValidationError(words["currency"].format(currency=order["currency"].strip().upper(), own=own))
    lines, gross, rates = [], [], []
    for line in order["lines"]:
        item = _item(line["sku"])
        if item is None:
            raise ValidationError(words["sku"].format(sku=line["sku"] or "—"))
        quantity, price = _decimal(line["quantity"], words), _decimal(line["price"], words)
        if quantity <= 0 or price < 0:
            raise ValidationError(words["number"])
        rate = Decimal((options.get("rates") or {}).get(str(item.pk), "0")) if options.get("with_vat") else ZERO
        gross.append(quantity * price)
        rates.append(rate)
        # Codex #273: the line's amount before tax is worked out on the whole line (the store's price had the VAT in it),
        # not from a rounded unit price times the quantity; the unit price is rounded up to the piastre and the few
        # piastres over come off as the line's discount, so the line still comes to what the store charged.
        net = quantity * price * HUNDRED / (HUNDRED + rate) if rate else quantity * price
        unit = (net / quantity).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        lines.append({"item": item, "quantity": quantity, "unit_sale_price": unit, "line_discount_amount": ZERO, "description": "",
                      "serials": line.get("serials", ""), "_over": money_round(quantity * unit) - money_round(net)})
    _spread_discount(lines, gross, rates, money_round(_decimal(order["discount"], words, ZERO)))
    for line in lines:
        over = line.pop("_over")
        line["line_discount_amount"] = min(line["line_discount_amount"] + over, money_round(line["quantity"] * line["unit_sale_price"]))
    lines = prepare_sale_serials(lines)  # Codex #273: a serial-tracked item needs its serials, as on the invoice form
    customer, added = _customer(order, user, words, phones)
    notes = [words["notes"].format(source=StoreSource(source).label.split(" (")[0], number=order["number"])]
    shipping = _decimal(order["shipping"], words, ZERO)
    if shipping and options.get("shipping_item") is not None:
        fee = options["shipping_item"]
        rate = Decimal((options.get("rates") or {}).get(str(fee.pk), "0")) if options.get("with_vat") else ZERO
        shipping = money_round(shipping * HUNDRED / (HUNDRED + rate)) if rate else money_round(shipping)  # Codex #273: as the prices
        lines.append({"item": fee, "quantity": Decimal("1"), "unit_sale_price": shipping, "line_discount_amount": ZERO,
                      "description": words["shipping_line"]})
    elif shipping:
        notes.append(words["shipping"].format(amount=f"{shipping:,.2f}"))
    if customer.customer_code == WALK_IN_CODE and (order["customer"] or order["phone"]):
        notes.append(words["who"].format(who=" ".join(part for part in (order["customer"], order["phone"], order["address"]) if part)))
    header = {"invoice_date": _day(order["date"]), "customer": customer, "selling_location": location, "cashbox": None,
              "paid_now": ZERO, "discount_amount": ZERO, "notes": " ".join(notes)}
    for _attempt in range(5):  # Codex #273: Hesba's own number; the store's stays in the notes and on StoreOrder
        try:
            with transaction.atomic():
                invoice = create_sales_draft_with_tax(dict(header, invoice_number=next_in_series(SalesInvoice, "invoice_number", "WEB-")), lines, user)
            break
        except IntegrityError:
            continue
    else:
        raise ValidationError(words["taken"].format(number="WEB-"))
    attach_sale_serials(invoice, lines)
    store_total = _decimal(order["total"], words, ZERO) if order["total"] else None
    try:
        with transaction.atomic():
            StoreOrder.objects.create(source=source, order_number=order["number"][:60], invoice=invoice, store_total=store_total, imported_by=user)
    except IntegrityError:
        raise _AlreadyImported  # Codex #273: another upload took this order meanwhile; this draft goes back with it
    return invoice, added


def shipping_items():
    """Items that can carry the store's shipping charge: active, not kept in stock (a service)."""

    from master_data.models import Item

    return Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")


def _replace(found, words):
    """Codex #273: an imported order whose draft was never posted is uploaded again to correct it: the draft goes."""

    from django.db.models import ProtectedError

    invoice = found.invoice
    if invoice.status != "draft":
        raise ValidationError(words["posted"])
    found.delete()
    try:
        invoice.delete()  # a draft has no stock, cash or ledger effect; its lines, tax rows and serial links go with it
    except ProtectedError:
        raise ValidationError(words["posted"])


def import_orders(raw, location, user, lang="ar", shipping_item=None, with_vat=False, replace=False):
    """Each order of the file as a draft sales invoice. Returns {"source", "made": [...], "skipped": [...], "again": [...]}.

    ``shipping_item``: a service item the store's shipping charge goes on (else it is noted); ``with_vat``: the store's
    prices include VAT; ``replace``: an order imported before whose draft is not posted yet is made again."""

    if not can_import(user):
        raise PermissionDenied(f"Importing store orders needs {IMPORT}.")
    words = _words(lang)
    if location is None:
        raise ValidationError(words["location"])
    from taxes.services import rates_by_item

    from settings_core.models import ClientProfile

    options = {"shipping_item": shipping_item, "with_vat": with_vat, "rates": rates_by_item() if with_vat else None,
               "currency": getattr(ClientProfile.get_active(), "default_currency", None) or "EGP"}
    source, orders = read_orders(raw, lang)
    made, skipped, again = [], [], []
    phones = phone_index()
    from entities import scope as entity_scope
    from sales.models import SalesInvoice

    mine = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE)
    for order in orders.values():
        found = StoreOrder.objects.filter(source=source, order_number=order["number"][:60]).select_related("invoice").first()
        if found is not None and not replace:
            again.append(order["number"])
            continue
        if found is not None and not mine.filter(pk=found.invoice_id).exists():
            skipped.append({"number": order["number"], "reason": words["elsewhere"]})  # Codex #273: another entity's order stays untouched
            continue
        trial = dict(phones)  # a customer added for an order that fails is rolled back with it
        try:
            with transaction.atomic():
                if found is not None:
                    _replace(found, words)
                invoice, added = _draft(source, order, location, user, words, trial, options)
        except ValidationError as exc:
            skipped.append({"number": order["number"], "reason": " ".join(exc.messages)})
        except _AlreadyImported:
            again.append(order["number"])
        else:
            phones.update(trial)
            made.append({"number": order["number"], "invoice": invoice, "added_customer": added, "store_total": invoice.store_order.store_total,
                         "replaced": found is not None})
    return {"source": source, "made": made, "skipped": skipped, "again": again}


def recent_orders():
    """Imported orders of the entity being worked in, newest first."""

    from entities import scope as entity_scope

    return entity_scope.scope(StoreOrder.objects.select_related("invoice__customer"), "invoice__" + entity_scope.SALES_INVOICE)[:30]
