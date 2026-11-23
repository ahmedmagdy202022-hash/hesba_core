"""SERIAL-001: receive, sell, return and look up serial numbers; warranty dates.

A serial's state is always read from the documents that carry it:

* **pending**  — on a purchase draft;
* **in stock** — registered by hand, or on a posted purchase, and not sold
  (every posted sale of it has a posted return);
* **sold**     — on a posted sale that has not come back;
* **void**     — its purchase was cancelled;
* **retired**  — taken out by hand (returned to the supplier, damaged...).

Cancelling a sale or a return therefore needs no clean-up here.
"""

import calendar
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q

from audit.models import AuditEventType, AuditLog
from settings_core.capabilities import capability_enabled

from .models import SerialNumber, SerialReturn, SerialSale, SerialSetting


MAX_SERIALS_PER_LINE = 500
MESSAGES = {
    "ar": {
        "count": "«{item}»: عدد السيريالات ({got}) لازم يساوي الكمية ({want}).",
        "whole": "«{item}» بيتباع بالسيريال، فالكمية لازم رقم صحيح.",
        "untracked": "«{item}» مش متتبع بالسيريال؛ امسح السيريالات أو فعّل التتبع للصنف.",
        "taken": "السيريال {serial} متسجل قبل كده.",
        "repeat": "السيريال {serial} مكرر.",
        "missing": "السيريال {serial} مش في المخزون لـ«{item}».",
        "too_many": "سيريالات كتير في سطر واحد.",
    },
    "en": {
        "count": "“{item}”: the number of serials ({got}) must equal the quantity ({want}).",
        "whole": "“{item}” is sold by serial number, so its quantity must be a whole number.",
        "untracked": "“{item}” is not tracked by serial; remove the serials or turn tracking on for the item.",
        "taken": "Serial {serial} is already recorded.",
        "repeat": "Serial {serial} appears twice.",
        "missing": "Serial {serial} is not in stock for “{item}”.",
        "too_many": "Too many serials on one line.",
    },
}


def serials_enabled():
    return capability_enabled("serials")


def normalize(serial):
    return re.sub(r"\s+", "", serial or "").upper()[:80]


def parse_serials(raw):
    """'356938 035643809, 35693803564381' -> ['356938035643809', ...]. Newline, comma or semicolon separated."""

    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = re.split(r"[,،;\n\r]+", raw or "")
    return [value for value in (normalize(part) for part in parts) if value]


def tracked_settings(item_ids=None):
    rows = SerialSetting.objects.filter(tracked=True)
    if item_ids is not None:
        rows = rows.filter(item_id__in=item_ids)
    return {row.item_id: row for row in rows}


def _with_state(queryset):
    return queryset.annotate(
        sold_count=Count("sales", filter=Q(sales__sales_line__invoice__status="posted"), distinct=True),
        returned_count=Count("returns", filter=Q(returns__return_line__sales_return__status="posted"), distinct=True),
    )


def state_of(serial):
    if not serial.active:
        return "retired"
    if serial.purchase_line_id:
        status = serial.purchase_line.invoice.status
        if status == "cancelled":
            return "void"
        if status != "posted":
            return "pending"
    sold = getattr(serial, "sold_count", None)
    returned = getattr(serial, "returned_count", None)
    if sold is None:
        sold = serial.sales.filter(sales_line__invoice__status="posted").count()
        returned = serial.returns.filter(return_line__sales_return__status="posted").count()
    return "sold" if sold > returned else "in_stock"


def available_serials(item_ids=None):
    """Serial numbers in stock now, as a queryset."""

    queryset = SerialNumber.objects.filter(active=True).filter(Q(purchase_line__isnull=True) | Q(purchase_line__invoice__status="posted"))
    if item_ids is not None:
        queryset = queryset.filter(item_id__in=item_ids)
    queryset = _with_state(queryset)
    return queryset.filter(sold_count__lte=F("returned_count"))


def _live_duplicates(serials):
    """Serials already recorded and not void or retired."""

    rows = SerialNumber.objects.filter(serial__in=serials, active=True).select_related("purchase_line__invoice")
    return {row.serial for row in rows if not (row.purchase_line_id and row.purchase_line.invoice.status == "cancelled")}


def _whole(quantity):
    return quantity == quantity.to_integral_value()


def _check_count(item, serials, quantity, words):
    if not _whole(quantity):
        raise ValidationError(words["whole"].format(item=item.item_name))
    if len(serials) != int(quantity):
        raise ValidationError(words["count"].format(item=item.item_name, got=len(serials), want=int(quantity)))


def prepare_purchase_serials(lines, lang="ar"):
    """Validate the serials typed on purchase lines; returns the lines with ``serial_list``."""

    if not serials_enabled():
        return lines
    words = MESSAGES[lang]
    tracked = tracked_settings([line["item"].pk for line in lines])
    seen, result = set(), []
    for line in lines:
        serials = parse_serials(line.get("serials"))
        item = line["item"]
        if len(serials) > MAX_SERIALS_PER_LINE:
            raise ValidationError(words["too_many"])
        if item.pk in tracked:
            _check_count(item, serials, line["quantity"], words)
        elif serials:
            raise ValidationError(words["untracked"].format(item=item.item_name))
        for serial in serials:
            if serial in seen:
                raise ValidationError(words["repeat"].format(serial=serial))
            seen.add(serial)
        result.append(dict(line, serial_list=serials))
    taken = _live_duplicates(seen)
    if taken:
        raise ValidationError(words["taken"].format(serial=sorted(taken)[0]))
    return result


def attach_purchase_serials(invoice, lines, user=None):
    for line, data in zip(invoice.lines.order_by("line_number"), lines):
        for serial in data.get("serial_list") or ():
            SerialNumber.objects.create(item=line.item, serial=serial, purchase_line=line, received_on=invoice.invoice_date, created_by=user)


def prepare_sale_serials(lines, lang="ar", exclude_invoice=None):
    """Validate the serials on sales lines against stock; returns lines with ``serial_objs``.

    The serials are added to the line description, which the printout shows.
    """

    if not serials_enabled():
        return lines
    words = MESSAGES[lang]
    tracked = tracked_settings([line["item"].pk for line in lines])
    wanted = {}
    for line in lines:
        serials = parse_serials(line.get("serials"))
        item = line["item"]
        if len(serials) > MAX_SERIALS_PER_LINE:
            raise ValidationError(words["too_many"])
        if item.pk in tracked:
            _check_count(item, serials, line["quantity"], words)
        elif serials:
            raise ValidationError(words["untracked"].format(item=item.item_name))
        for serial in serials:
            if serial in wanted:
                raise ValidationError(words["repeat"].format(serial=serial))
            wanted[serial] = item
    stock = {row.serial: row for row in available_serials(list({item.pk for item in wanted.values()})).filter(serial__in=list(wanted))}
    for serial, item in wanted.items():
        row = stock.get(serial)
        if row is None or row.item_id != item.pk:
            raise ValidationError(words["missing"].format(serial=serial, item=item.item_name))
    result = []
    for line in lines:
        serials = parse_serials(line.get("serials"))
        if not serials:
            result.append(line)
            continue
        description = (line.get("description") or "").strip() or line["item"].item_name
        result.append(dict(line, serial_objs=[stock[s] for s in serials], description=f"{description} — S/N: {', '.join(serials)}"[:255]))
    return result


def attach_sale_serials(invoice, lines):
    for line, data in zip(invoice.lines.order_by("line_number"), lines):
        for serial in data.get("serial_objs") or ():
            SerialSale.objects.create(serial=serial, sales_line=line)


def check_invoice_serials(invoice, lang="ar"):
    """Before a draft sale is posted: its serials must still be in stock."""

    words = MESSAGES[lang]
    rows = SerialSale.objects.filter(sales_line__invoice=invoice).select_related("serial", "sales_line__item")
    if not rows:
        return
    in_stock = set(available_serials().filter(pk__in=[row.serial_id for row in rows]).values_list("pk", flat=True))
    for row in rows:
        if row.serial_id not in in_stock:
            raise ValidationError(words["missing"].format(serial=row.serial.serial, item=row.sales_line.item.item_name))


def _audit(action, serial_ids, user, after, reason=""):
    AuditLog.objects.create(
        event_type=AuditEventType.CREATE if action == "register_serials" else AuditEventType.UPDATE, actor=user, module="serials", action=action,
        object_type="serials.SerialNumber", object_id=",".join(str(pk) for pk in serial_ids)[:100], before_data={}, after_data=after, reason=reason,
    )


@transaction.atomic
def register_serials(item, serials, received_on, user, lang="ar", note=""):
    """Serial numbers for units already on the shelf."""

    words = MESSAGES[lang]
    serials = parse_serials(serials)
    if len(set(serials)) != len(serials):
        raise ValidationError(words["repeat"].format(serial=next(s for s in serials if serials.count(s) > 1)))
    taken = _live_duplicates(serials)
    if taken:
        raise ValidationError(words["taken"].format(serial=sorted(taken)[0]))
    created = [SerialNumber.objects.create(item=item, serial=serial, received_on=received_on, note=note[:255], created_by=user) for serial in serials]
    if created:
        _audit("register_serials", [row.pk for row in created], user, {"item": item.item_code, "serials": serials})
    return created


@transaction.atomic
def record_return(serial, return_line, user):
    """A sold serial came back on a posted sales return of its invoice."""

    serial = SerialNumber.objects.select_for_update().get(pk=serial.pk)
    if return_line.sales_return.status != "posted":
        raise ValidationError("The sales return is not posted.")
    if not SerialSale.objects.filter(serial=serial, sales_line=return_line.source_line, sales_line__invoice__status="posted").exists():
        raise ValidationError("This serial was not sold on the returned line.")
    if state_of(serial) != "sold":
        raise ValidationError("This serial is not out with a customer.")
    if return_line.serials.count() >= return_line.quantity:
        raise ValidationError("Every returned unit on that line already has its serial.")
    SerialReturn.objects.create(serial=serial, return_line=return_line, created_by=user)
    _audit("return_serial", [serial.pk], user, {"serial": serial.serial, "sales_return": return_line.sales_return_id})


@transaction.atomic
def retire_serial(serial, user, reason):
    if not (reason or "").strip():
        raise ValidationError("A reason is required.")
    serial.active = False
    serial.save(update_fields=["active"])
    _audit("retire_serial", [serial.pk], user, {"serial": serial.serial, "active": False}, reason=reason.strip()[:255])


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def history(serial):
    """Everything known about one serial, newest sale last, with warranty."""

    purchase = serial.purchase_line.invoice if serial.purchase_line_id else None
    sales = [row for row in serial.sales.select_related("sales_line__invoice__customer").order_by("pk") if row.sales_line.invoice.status == "posted"]
    returns = [row for row in serial.returns.select_related("return_line__sales_return").order_by("pk") if row.return_line.sales_return.status == "posted"]
    setting = SerialSetting.objects.filter(item_id=serial.item_id).first()
    months = setting.warranty_months if setting else 0
    last_sale = sales[-1].sales_line.invoice if sales else None
    warranty_until = add_months(last_sale.invoice_date, months) if last_sale and months else None
    return {
        "serial": serial, "state": state_of(serial), "purchase": purchase, "sales": [row.sales_line.invoice for row in sales],
        "returns": [row.return_line.sales_return for row in returns], "last_sale": last_sale, "warranty_months": months, "warranty_until": warranty_until,
    }


def pos_serial_catalog():
    """For the till: tracked item ids and the serials in stock, or None when off."""

    if not serials_enabled():
        return None
    tracked = list(tracked_settings())
    return {"tracked": tracked, "serials": {row.serial: row.item_id for row in available_serials(tracked).only("serial", "item_id")}}
