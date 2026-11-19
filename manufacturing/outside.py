"""Manufacturing F4 (HG-137): a stage of an order done at an outside supplier («تشغيل لدى الغير»).

Rules, stated once:

* **Sending moves no stock.** An order on the floor already has its units in work in progress (its materials were
  issued when it started), so sending its current stage to a supplier is a record of where the units are
  (``OutsideJob``): no stock movement, no stock operation, no journal row. The whole stage goes out: the quantity
  sent is the units that passed the stages before it. The first stage is never sent: the order must be on the floor,
  because closing the first stage is what issues the materials.
* **The agreed price** (per good unit that comes back, before tax) is typed by someone who may see cost
  (``inventory.view_cost``), when the stage is sent or any time before its bill; a stage sent by someone who may
  not see cost carries no price until then, and a bill needs one.
* **While a stage is out it cannot be closed in the factory** (``orders.advance`` asks ``check_in_house``). It is
  closed by its return: good and defective, exactly like a stage (the defective units are scrap, as ever), through
  the same ``quality.advance`` the order page uses. Good and defective together are never more than was sent.
* **The supplier's bill** is an ordinary **draft** purchase invoice made by the unchanged
  ``taxes.services.create_purchase_draft_with_tax``: one service line (the reserved service item «تشغيل لدى الغير»)
  of the good units that came back × the agreed price. It is posted, cancelled and returned from the purchases
  screen as any purchase; nothing of purchase posting is touched. A draft bill made by mistake is withdrawn here
  (the draft is deleted, as a subcontractor's draft bill is in ``projects.costs``).
* **A bill cancelled from the purchases screen is read, never stored**: such a job counts as back and takes a new
  bill (``state``).
* **At the finish** (``absorb``, called by ``orders.finish``) every stage that came back with good units must have
  a **posted** bill: a bill still draft, or no bill at all, refuses the finish (owner question 11). Each posted
  bill, net of recoverable VAT and of its posted purchase returns (exactly what the ledger booked to general
  expenses for it), is written as ``OrderCostLine(kind=outside)`` and so joins the order's conversion
  (``actuals.conversion``) and the product's stock cost.
* **The ledger needs no new rule.** The posted service purchase books to general expenses (5299,
  ``projector._service_cost``); at the finish the fee is part of the run's conversion, which the projector already
  credits to absorbed production cost (5102). Profit and loss is neutral until the goods are sold.

No line of the inventory engine, purchase posting, the projector or average cost is changed here.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import qty as fmt_qty

from .models import CostLineKind, OrderCostLine, OrderStatus, OutsideJob, OutsideStatus

ZERO = Decimal("0")
PRODUCE, COST, BILL = "inventory.adjust_stock", "inventory.view_cost", "purchases.create_purchase_invoice"
SERVICE_CODE = "MFG-OUT"
SERVICE_NAME = "تشغيل لدى الغير"
DRAFT, POSTED, CANCELLED = "draft", "posted", "cancelled"

MESSAGES = {
    "ar": {
        "permission": "إرسال مرحلة لجهة تشغيل واستلامها محتاج صلاحية تسوية المخزون.",
        "price_permission": "سعر التشغيل بيدخل في تكلفة المنتج، فتحديده محتاج صلاحية رؤية التكلفة.",
        "bill_permission": "فاتورة التشغيل محتاجة صلاحية تسجيل فواتير الشراء.",
        "planned": "أول مرحلة بتتسجل في المصنع عشان الخامات تتصرف على الأمر؛ المراحل اللي بعدها هي اللي ممكن تتبعت لجهة تشغيل.",
        "state": "الأمر ده مش في مرحلة ينفع تتبعت لجهة تشغيل.",
        "quality_stage": "مرحلة فحص الجودة ليها معايير بتتسجل في المصنع، فمينفعش تتبعت لجهة تشغيل.",
        "supplier": "اختار المورّد اللي هيشغّل المرحلة.",
        "price": "سعر الوحدة المتفق عليه لازم يكون رقم أكبر من صفر.",
        "price_state": "سعر التشغيل بيتغيّر لحد ما فاتورة التشغيل تتعمل بس.",
        "no_price": "سعر الوحدة المتفق عليه لسه متحددش على المرحلة دي؛ حدّده من صفحة الأمر وبعدين اعمل فاتورة التشغيل.",
        "nothing": "مفيش وحدات سليمة داخلة المرحلة دي عشان تتبعت.",
        "already": "المرحلة دي متسجلة عند جهة تشغيل قبل كده.",
        "date": "التاريخ مش صحيح.", "future": "التاريخ مينفعش يكون بعد النهارده.",
        "at_supplier": "المرحلة دي عند «{supplier}»؛ سجّل رجوعها من قسم «تشغيل لدى الغير» في صفحة الأمر.",
        "not_out": "المرحلة دي مش عند جهة التشغيل دلوقتي.",
        "quantity": "الكمية السليمة والكمية المعيبة لازم يكونوا أرقام مش سالبة.",
        "over": "الكمية المرسلة {sent} بس؛ السليم والمعيب مع بعض مينفعش يزيدوا عنها.",
        "before_sent": "تاريخ الرجوع مينفعش يكون قبل تاريخ الإرسال.",
        "not_back": "فاتورة التشغيل بتتعمل بعد ما المرحلة ترجع من جهة التشغيل.",
        "billed": "المرحلة دي ليها فاتورة تشغيل قبل كده.",
        "nothing_good": "مرجعش من المرحلة دي وحدات سليمة، فمفيش فاتورة تشغيل عليها.",
        "no_draft": "مفيش فاتورة مسودة على المرحلة دي.",
        "not_draft": "الفاتورة اترحّلت؛ لو محتاج تلغيها، الإلغاء من شاشة المشتريات.",
        "withdraw_blocked": "الفاتورة مرتبطة بمستندات تانية، فمينفعش تتسحب.",
        "cancel_state": "إلغاء الإرسال ممكن قبل ما المرحلة ترجع بس.",
        "finish_out": "مرحلة «{stage}» لسه عند «{supplier}»؛ سجّل رجوعها الأول.",
        "finish_no_bill": "مرحلة «{stage}» رجعت من «{supplier}» ولسه ملهاش فاتورة تشغيل؛ اعمل الفاتورة ورحّلها وبعدين أنهِ الأمر.",
        "finish_draft": "فاتورة التشغيل {number} من «{supplier}» لسه مترحّلتش؛ رحّلها من شاشة المشتريات وبعدين أنهِ الأمر.",
        "cancel_order_out": "الأمر ليه مرحلة عند «{supplier}»؛ سجّل رجوعها أو ألغِ الإرسال قبل ما تلغي الأمر.",
    },
    "en": {
        "permission": "Sending a stage to an outside supplier and taking it back needs the stock adjustment permission.",
        "price_permission": "The processing price joins the product's cost, so setting it needs the permission to see costs.",
        "bill_permission": "The processing bill needs the permission to create purchase invoices.",
        "planned": "The first stage is recorded in the factory, so the materials are issued to the order; the stages after it can go to an outside supplier.",
        "state": "This order is not at a stage that can go to an outside supplier.",
        "quality_stage": "The quality stage has criteria recorded in the factory, so it cannot go to an outside supplier.",
        "supplier": "Choose the supplier who will process the stage.",
        "price": "The agreed price per unit must be a number above zero.",
        "price_state": "The processing price can change only until the processing bill is made.",
        "no_price": "This stage has no agreed price per unit yet; set it on the order page, then make the processing bill.",
        "nothing": "No good units enter this stage, so there is nothing to send.",
        "already": "This stage is already recorded at an outside supplier.",
        "date": "The date is not valid.", "future": "The date cannot be after today.",
        "at_supplier": "This stage is at “{supplier}”; record its return in the “Outside processing” section of the order page.",
        "not_out": "This stage is not at the outside supplier now.",
        "quantity": "The good and defective quantities must be numbers, not negative.",
        "over": "Good and defective together cannot be more than the quantity sent ({sent}).",
        "before_sent": "The return date cannot be before the date it was sent.",
        "not_back": "The processing bill is made after the stage comes back from the supplier.",
        "billed": "This stage already has a processing bill.",
        "nothing_good": "No good units came back from this stage, so there is no processing bill for it.",
        "no_draft": "This stage has no draft bill.",
        "not_draft": "The bill is posted; cancel it from the purchases screen if you need to.",
        "withdraw_blocked": "The bill is linked to other documents, so it cannot be withdrawn.",
        "cancel_state": "A sending can be cancelled only before the stage comes back.",
        "finish_out": "The stage “{stage}” is still at “{supplier}”; record its return first.",
        "finish_no_bill": "The stage “{stage}” came back from “{supplier}” and has no processing bill yet; make the bill and post it, then finish the order.",
        "finish_draft": "The processing bill {number} from “{supplier}” is not posted yet; post it from the purchases screen, then finish the order.",
        "cancel_order_out": "A stage of this order is at “{supplier}”; record its return or cancel the sending before cancelling the order.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _audit(job, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type="manufacturing.OutsideJob",
                            object_id=str(job.pk), before_data={}, after_data=after)


def _day(value, words):
    """A typed date (a ``date``, an ISO text, or blank for today); never after today."""

    from datetime import date

    today = timezone.localdate()
    if value in (None, ""):
        return today
    if not isinstance(value, date):
        try:
            value = date.fromisoformat(str(value))
        except ValueError:
            raise ValidationError(words["date"])
    if value > today:
        raise ValidationError(words["future"])
    return value


def _locked(job):
    """The job's order row, then the job row, both locked (the order first, as every other writer of an order does)."""

    from .orders import _locked as locked_order

    order = locked_order(job.order)
    return order, OutsideJob.objects.select_for_update(of=("self",)).select_related("supplier", "purchase_invoice").get(pk=job.pk)


# ---- what a job is, read from its own row and its bill ----

def state(job):
    """``sent``, ``back``, ``billed`` or ``cancelled``. A job whose bill was cancelled from the purchases screen is back."""

    if job.status == OutsideStatus.BILLED and (job.purchase_invoice_id is None or job.purchase_invoice.status == CANCELLED):
        return OutsideStatus.BACK
    return job.status


def bill_state(job):
    """``draft`` or ``posted`` for a job with a live bill, else an empty string."""

    return job.purchase_invoice.status if state(job) == OutsideStatus.BILLED else ""


def bill_amount(job):
    """What the bill charges before tax: the good units that came back × the agreed price."""

    return money_round(job.returned_good * job.unit_price)


def bill_cost(invoice):
    """What a posted bill left in expenses: its total less its recoverable VAT, less what its posted purchase returns
    took back (their totals less their VAT). The same figures the ledger books to general expenses for the invoice
    and for each of its returns (``projector._place_purchase_invoice``, ``_place_purchase_return``)."""

    from taxes.models import PurchaseReturnLineTax

    cost = invoice.total_amount - invoice.tax_amount
    for purchase_return in invoice.returns.filter(status=POSTED):
        tax = PurchaseReturnLineTax.objects.filter(return_line__purchase_return=purchase_return).aggregate(total=Sum("tax_amount"))["total"] or ZERO
        cost -= purchase_return.total_amount - tax
    return money_round(max(cost, ZERO))


def live_jobs(order):
    """The order's jobs that were not cancelled, oldest first, with their supplier and bill."""

    return list(order.outside_jobs.exclude(status=OutsideStatus.CANCELLED).select_related("supplier", "purchase_invoice", "cost_line").order_by("pk"))


def current_job(order):
    """The job of the stage that is at a supplier now, else None."""

    return order.outside_jobs.filter(status=OutsideStatus.SENT).select_related("supplier").first()


def stage_name(order, key, lang="ar"):
    for stage in order.stages:
        if stage[0] == key:
            return stage[2] if lang == "en" and stage[2] else stage[1]
    return key


def expected(order):
    """What outside processing is expected to cost an order that has not finished: a posted bill at what it left in
    expenses, a stage that is back at its good units × the agreed price, a stage still out at the quantity sent × the
    agreed price. A figure for the order's cost card only; the finish reads posted bills alone (``absorb``)."""

    total = ZERO
    for job in live_jobs(order):
        where = state(job)
        if where == OutsideStatus.SENT:
            total += money_round(job.quantity_sent * job.unit_price)
        elif where == OutsideStatus.BILLED and job.purchase_invoice.status == POSTED:
            total += bill_cost(job.purchase_invoice)
        else:
            total += bill_amount(job)
    return money_round(total)


def scoped_jobs():
    """The jobs of the entity being worked in: a job belongs to the entity of its order's location."""

    from entities import scope as entity_scope

    return entity_scope.scope(OutsideJob.objects, "order__location__entity")


# ---- sending a stage out ----

def can_send(order):
    """Whether the order's current stage may go out now (the rules ``send`` enforces, for the page)."""

    from . import quality

    return (order.status == OrderStatus.IN_PROGRESS and order.stage_index < len(order.stages) and not quality.stage_criteria(order)
            and not order.outside_jobs.filter(stage_key=order.stages[order.stage_index][0]).exclude(status=OutsideStatus.CANCELLED).exists())


def _price(raw, user, words):
    """A typed agreed price: above zero, to the piastre, and only from someone who may see cost."""

    from . import services

    if not user_has_permission(user, COST):
        raise PermissionDenied(words["price_permission"])
    price = services._decimal(raw)
    if price is None or money_round(price) <= 0:
        raise ValidationError(words["price"])
    return money_round(price)


@transaction.atomic
def send(order, user, *, supplier, unit_price=None, sent_on=None, note="", lang="ar"):
    """«ابعت المرحلة لجهة تشغيل»: the order's current stage goes to ``supplier``, at ``unit_price`` per good unit that
    comes back when it is already agreed (blank: set later with ``set_price``). A record only: nothing moves in stock."""

    from . import quality
    from .orders import _locked as locked_order, good_units

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["permission"])
    price = ZERO if unit_price in (None, "") else _price(unit_price, user, words)
    order = locked_order(order)
    if order.status == OrderStatus.PLANNED:
        raise ValidationError(words["planned"])
    if order.status != OrderStatus.IN_PROGRESS or order.stage_index >= len(order.stages):
        raise ValidationError(words["state"])
    if quality.stage_criteria(order):
        raise ValidationError(words["quality_stage"])
    if supplier is None or not supplier.active:
        raise ValidationError(words["supplier"])
    day = _day(sent_on, words)
    stage = order.stages[order.stage_index]
    if order.outside_jobs.filter(stage_key=stage[0]).exclude(status=OutsideStatus.CANCELLED).exists():
        raise ValidationError(words["already"])
    units = good_units(order)
    if units <= 0:
        raise ValidationError(words["nothing"])
    job = OutsideJob.objects.create(order=order, stage_key=stage[0], supplier=supplier, quantity_sent=units, unit_price=price, sent_on=day,
                                    note=(note or "").strip()[:255], created_by=user)
    _audit(job, user, "send_stage_out", {"order": order.number, "stage": stage[0], "supplier": supplier.supplier_code, "quantity": str(units),
                                          "unit_price": str(job.unit_price), "sent_on": day.isoformat()}, AuditEventType.CREATE)
    return job


@transaction.atomic
def set_price(job, user, unit_price, lang="ar"):
    """The agreed price per good unit, set or changed until the bill is made. Needs ``inventory.view_cost``."""

    words = _words(lang)
    price = _price(unit_price, user, words)
    _order, job = _locked(job)
    if state(job) not in (OutsideStatus.SENT, OutsideStatus.BACK):
        raise ValidationError(words["price_state"])
    before = job.unit_price
    job.unit_price = price
    job.save(update_fields=["unit_price"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action="price_stage_out", object_type="manufacturing.OutsideJob",
                            object_id=str(job.pk), before_data={"unit_price": str(before)}, after_data={"unit_price": str(price)})
    return job


def check_in_house(order, lang="ar"):
    """``orders.advance`` asks this: a stage that is at a supplier is closed by its return, not in the factory."""

    job = current_job(order)
    if job is not None:
        raise ValidationError(_words(lang)["at_supplier"].format(supplier=job.supplier.name))


def check_order_cancel(order, lang="ar"):
    """``orders.cancel`` asks this: the materials cannot come back to the shelf while the units are at a supplier."""

    job = current_job(order)
    if job is not None:
        raise ValidationError(_words(lang)["cancel_order_out"].format(supplier=job.supplier.name))


@transaction.atomic
def cancel_sending(job, user, lang="ar"):
    """A sending recorded by mistake: the stage is back in the factory as if it had never gone. Only before it returns."""

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["permission"])
    _order, job = _locked(job)
    if job.status != OutsideStatus.SENT:
        raise ValidationError(words["cancel_state"])
    job.status = OutsideStatus.CANCELLED
    job.save(update_fields=["status"])
    _audit(job, user, "cancel_stage_out", {"order": job.order.number, "stage": job.stage_key})
    return job


# ---- the stage comes back ----

def _count(raw, words):
    from . import services

    number = services._decimal(raw)
    if number is None or number < 0:
        raise ValidationError(words["quantity"])
    return number


@transaction.atomic
def receive(job, user, *, good=None, defect=None, returned_on=None, note="", lang="ar"):
    """«سجّل الرجوع»: the stage is back from the supplier with ``good`` and ``defect`` units, and is closed like any
    stage (the defective units are scrap). Nothing moves in stock: the units never left work in progress."""

    from . import quality

    words = _words(lang)
    if not user_has_permission(user, PRODUCE):
        raise PermissionDenied(words["permission"])
    order, job = _locked(job)
    if job.status != OutsideStatus.SENT:
        raise ValidationError(words["not_out"])
    if (order.status != OrderStatus.IN_PROGRESS or order.stage_index >= len(order.stages)
            or order.stages[order.stage_index][0] != job.stage_key):
        raise ValidationError(words["not_out"])
    defect_qty = ZERO if defect in (None, "") else _count(defect, words)
    good_qty = max(job.quantity_sent - defect_qty, ZERO) if good in (None, "") else _count(good, words)
    if good_qty + defect_qty > job.quantity_sent:
        raise ValidationError(words["over"].format(sent=fmt_qty(job.quantity_sent)))
    day = _day(returned_on, words)
    if day < job.sent_on:
        raise ValidationError(words["before_sent"])
    job.returned_good, job.returned_defective, job.returned_on, job.status = good_qty, defect_qty, day, OutsideStatus.BACK
    job.save(update_fields=["returned_good", "returned_defective", "returned_on", "status"])
    # The same close as the order page's «خلّص المرحلة دي» (the job is back, so ``check_in_house`` lets it through).
    quality.advance(order, user, good=good_qty, defect=defect_qty, note=(note or "").strip(), lang=lang)
    _audit(job, user, "receive_stage_back", {"order": order.number, "stage": job.stage_key, "good": str(good_qty), "defect": str(defect_qty),
                                              "returned_on": day.isoformat()})
    return job


# ---- the supplier's bill ----

def service_item():
    """The reserved service line a processing bill uses: never a stock item, so the bill moves no stock."""

    from projects.services import service_item as reserved

    return reserved(SERVICE_CODE, SERVICE_NAME)


def _bill_location(order):
    """Where the bill is «received»: a service line moves no stock, so this only places the bill in the order's entity."""

    from master_data.models import Location

    if order.location.is_receiving_location:
        return order.location
    return (Location.objects.filter(active=True, is_receiving_location=True, entity_id=order.location.entity_id).order_by("-is_default", "pk").first()
            or order.location)


def _bill_number(order):
    from purchases.models import PurchaseInvoice

    stem = f"OUT-{order.number.removeprefix('PO-')}"  # OUT-20261118-004-01: the order's own date and serial, then the bill's
    sequence = 1
    while PurchaseInvoice.objects.filter(invoice_number=f"{stem}-{sequence:02d}").exists():
        sequence += 1
    return f"{stem}-{sequence:02d}"


@transaction.atomic
def make_bill(job, user, *, bill_date=None, lang="ar"):
    """«اعمل فاتورة التشغيل»: a **draft** purchase invoice from the job's supplier, one service line of the good units
    that came back × the agreed price, through the unchanged ``create_purchase_draft_with_tax``. It posts from the
    purchases screen."""

    from taxes.services import create_purchase_draft_with_tax

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill_permission"])
    order, job = _locked(job)
    where = state(job)
    if where == OutsideStatus.BILLED:
        raise ValidationError(words["billed"])
    if where != OutsideStatus.BACK:
        raise ValidationError(words["not_back"])
    if job.returned_good <= 0:
        raise ValidationError(words["nothing_good"])
    if job.unit_price <= 0:
        raise ValidationError(words["no_price"])
    day = _day(bill_date, words)
    label = f"أمر الإنتاج {order.number}: تشغيل مرحلة «{stage_name(order, job.stage_key)}» لدى الغير"
    invoice = create_purchase_draft_with_tax(
        {"invoice_number": _bill_number(order), "invoice_date": day, "supplier": job.supplier, "receiving_location": _bill_location(order),
         "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": label[:255]},
        [{"item": service_item(), "quantity": job.returned_good, "unit_purchase_price": job.unit_price, "line_discount_amount": ZERO,
          "description": label[:255]}], user)
    job.purchase_invoice, job.status = invoice, OutsideStatus.BILLED
    job.save(update_fields=["purchase_invoice", "status"])
    _audit(job, user, "bill_stage_out", {"order": order.number, "stage": job.stage_key, "invoice": invoice.invoice_number,
                                          "quantity": str(job.returned_good), "unit_price": str(job.unit_price), "total": str(invoice.total_amount)})
    return invoice


@transaction.atomic
def withdraw_bill(job, user, lang="ar"):
    """A draft bill made by mistake: the draft is deleted and the job is back, ready for a new bill. A posted bill is
    cancelled from the purchases screen, under that screen's own rules."""

    from purchases.models import PurchaseInvoice

    words = _words(lang)
    if not user_has_permission(user, BILL):
        raise PermissionDenied(words["bill_permission"])
    _order, job = _locked(job)
    if job.status != OutsideStatus.BILLED or job.purchase_invoice_id is None:
        raise ValidationError(words["no_draft"])
    invoice = PurchaseInvoice.objects.select_for_update(of=("self",)).get(pk=job.purchase_invoice_id)
    if invoice.status == CANCELLED:
        raise ValidationError(words["no_draft"])
    if invoice.status != DRAFT:
        raise ValidationError(words["not_draft"])
    number = invoice.invoice_number
    job.purchase_invoice, job.status = None, OutsideStatus.BACK
    job.save(update_fields=["purchase_invoice", "status"])
    try:
        with transaction.atomic():
            invoice.delete()
    except ProtectedError:
        raise ValidationError(words["withdraw_blocked"])
    _audit(job, user, "withdraw_stage_bill", {"order": job.order.number, "stage": job.stage_key, "invoice": number})
    return job


# ---- the finish ----

def blockers(order, lang="ar"):
    """Why the order cannot finish yet, one line per stage: still out, back with no bill, or billed and not posted."""

    words = _words(lang)
    found = []
    for job in live_jobs(order):
        where = state(job)
        names = {"stage": stage_name(order, job.stage_key, lang), "supplier": job.supplier.name}
        if where == OutsideStatus.SENT:
            found.append(words["finish_out"].format(**names))
        elif job.cost_line_id or job.returned_good <= 0:
            continue  # already in the order's cost, or nothing good came back to pay for
        elif where == OutsideStatus.BACK:
            found.append(words["finish_no_bill"].format(**names))
        elif job.purchase_invoice.status != POSTED:
            found.append(words["finish_draft"].format(number=job.purchase_invoice.invoice_number, **names))
    return found


def absorb(order, user, lang="ar"):
    """``orders.finish`` calls this inside its own transaction, on the locked order, before it reads the conversion.

    Refuses while a stage is out, back without a bill, or billed with the bill still draft (owner question 11). Then
    each posted bill, net of VAT and of its posted purchase returns, becomes one ``OrderCostLine(kind=outside)``."""

    refused = blockers(order, lang)
    if refused:
        raise ValidationError(refused[0])
    lines = []
    for job in live_jobs(order):
        if job.cost_line_id or state(job) != OutsideStatus.BILLED:
            continue
        cost = bill_cost(job.purchase_invoice)
        if cost <= 0:
            continue  # the whole bill went back on purchase returns: nothing joined the cost
        line = OrderCostLine.objects.create(order=order, kind=CostLineKind.OUTSIDE, amount=cost, created_by=user,
                                            description=f"{SERVICE_NAME}: {job.supplier.name} · {job.purchase_invoice.invoice_number}"[:255])
        job.cost_line = line
        job.save(update_fields=["cost_line"])
        _audit(job, user, "absorb_stage_bill", {"order": order.number, "stage": job.stage_key, "invoice": job.purchase_invoice.invoice_number,
                                                 "amount": str(cost)})
        lines.append(line)
    return lines


def drift(job):
    """For a job whose fee joined a finished order's cost: what its bill leaves in expenses now, less what was absorbed.
    Not zero when the bill was cancelled or returned from the purchases screen after the finish (the product's cost
    does not follow)."""

    if job.cost_line_id is None:
        return ZERO
    invoice = job.purchase_invoice
    now = bill_cost(invoice) if invoice is not None and invoice.status == POSTED else ZERO
    return money_round(now - job.cost_line.amount)
