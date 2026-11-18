"""Manufacturing F5 (HG-136): quality checks, a hold before goods can be sold, release and reject.

Rules, stated once:

* **The hold is a place, not a stock status.** Each entity gets one quality hold place, ``QC-<entity id>``, made the
  first time it is needed (as HG-037 makes ``TRANSIT-<entity>``). It is a receiving location and never a selling one:
  the sales invoice form, the till and the offline till only offer selling locations, so what waits there cannot be
  sold. The sales posting service itself does not check; any new sale path must keep that filter (plan trap 14).
* **Finished goods on hold.** An order whose recipe says ``needs_release`` finishes into the hold place
  (``orders.finish`` passes it to ``services.produce(output_location=...)``). Its materials still leave the order's
  location and the run keeps the order's location; only the product's own stock operation points at the hold place.
* **A check** is a dated record of the recipe's criteria and a decision on what waits:
  - the **passed** quantity is released to the order's location with the engine's own ``transfer_stock`` (value
    unchanged, no ledger value change; needs ``inventory.transfer_stock``);
  - the **rejected** quantity leaves the hold place with the engine's own ``adjust_stock`` out, reason
    «مرفوض في فحص الجودة <check>», which the ledger books as stock-count loss (5211) by its existing rule: an abnormal
    loss written off, not carried by the good units (needs ``inventory.adjust_stock``);
  - what is neither passed nor rejected **stays on hold**; a check that decides nothing needs a note.
* **What still waits is never stored.** It is the quantity made, less the passed and rejected quantities of the checks
  whose stock operations are still posted, and never more than the hold place really holds. So a release or a reject
  reversed from the stock operations screen (under that screen's own rules) simply puts the goods back in the queue.
* **A failed criterion** cannot be recorded with a passed quantity unless a note says why; a release needs every
  criterion answered.
* **Incoming checks.** A purchase invoice received into the hold place (chosen on the invoice as its receiving
  location; purchase posting is not touched) is checked per line the same way. Its passed quantity is released to a
  location chosen on the check; its rejected quantity **stays in the hold place** and goes back to the supplier on an
  ordinary purchase return, which takes it from the invoice's receiving location: the hold place itself.
* **In process.** An order's «فحص جودة» stage shows the recipe's criteria; they are recorded with the stage. Nothing
  moves: failed units are the stage's defects (scrap), exactly as before.
* **Lots (F3).** The product's lot is written at the hold place and is moved to where the goods were released, so
  per-location lot views follow the release. Group-wide positions were never affected by a transfer.

No stock, cost, average-cost, posting or ledger rule is changed here: every leg is an ordinary transfer or adjustment
the engine already knows. Rework is not built (owner question 14): rejected goods are written off or kept on hold.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField, Q, Sum, Value
from django.db.models.functions import Cast, Concat
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from master_data.models import Location
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import qty as fmt_qty

from . import services
from .models import CheckKind, CheckResult, CriterionKind, OrderStatus, ProductionOrder, QualityCheck, QualityCriterion, QualityResult, RunStatus

ZERO = Decimal("0")
RELEASE, REJECT, CRITERIA = "inventory.transfer_stock", "inventory.adjust_stock", "master_data.manage_items"
HOLD_PREFIX = "QC-"
QUALITY_STAGE = "quality"
CRITERIA_ROWS = 8
POSTED = "posted"

MESSAGES = {
    "ar": {
        "hold_name": "حجز الجودة", "nothing": "مفيش كمية منتظرة الفحص هنا.",
        "quantity": "الكمية المقبولة والكمية المرفوضة لازم يكونوا أرقام مش سالبة.",
        "over": "الكمية المنتظرة {waiting} بس؛ المقبول والمرفوض مع بعض مينفعش يزيدوا عنها.",
        "number": "قيمة «{name}» لازم تكون رقم.", "answer": "اختار مطابق أو غير مطابق في «{name}».",
        "answer_all": "سجّل نتيجة كل معيار قبل الإفراج.", "answer_stage": "سجّل نتيجة كل معيار قبل إنهاء مرحلة الفحص.",
        "failed_note": "في معيار غير مطابق؛ اكتب ملاحظة توضّح سبب قبول الكمية.",
        "hold_note": "اكتب ملاحظة توضّح سبب استمرار الحجز.",
        "release_permission": "تسجيل نتيجة الفحص محتاج صلاحية تحويل المخزون.",
        "reject_permission": "رفض كمية وشطبها من المخزون محتاج صلاحية تسوية المخزون.",
        "destination": "اختار المخزن اللي الكمية المقبولة هتتنقل له.",
        "criteria_permission": "تعديل معايير الجودة محتاج صلاحية إدارة الأصناف.",
        "criterion_name": "اكتب اسم المعيار بالعربي في السطر {row}.", "criterion_kind": "نوع المعيار في السطر {row} مش صحيح.",
        "criterion_limit": "حدود المعيار «{name}» لازم تكون أرقام، والحد الأدنى مش أكبر من الأقصى.",
        "plain_run": "الوصفة دي محتاجة إفراج جودة قبل البيع؛ شغّلها من أمر إنتاج عشان المنتج يدخل حجز الجودة.",
        "state": "الأمر ده مش في مرحلة فحص الجودة.",
    },
    "en": {
        "hold_name": "Quality hold", "nothing": "Nothing is waiting for a check here.",
        "quantity": "The passed and rejected quantities must be numbers, not negative.",
        "over": "Passed and rejected together cannot be more than the quantity waiting ({waiting}).",
        "number": "The value of “{name}” must be a number.", "answer": "Choose pass or fail for “{name}”.",
        "answer_all": "Record every criterion before releasing.", "answer_stage": "Record every criterion before closing the quality stage.",
        "failed_note": "A criterion failed; write a note that says why the quantity is accepted.",
        "hold_note": "Write a note that says why the goods stay on hold.",
        "release_permission": "Recording a check needs the stock transfer permission.",
        "reject_permission": "Rejecting a quantity and writing it off needs the stock adjustment permission.",
        "destination": "Choose the location the passed quantity moves to.",
        "criteria_permission": "Changing the quality criteria needs the items permission.",
        "criterion_name": "Write the Arabic name of the criterion on row {row}.", "criterion_kind": "The kind of the criterion on row {row} is not valid.",
        "criterion_limit": "The limits of “{name}” must be numbers, and the minimum not above the maximum.",
        "plain_run": "This recipe needs a quality release before sale; make it through a production order so the product enters the quality hold.",
        "state": "This order is not at its quality stage.",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _audit(obj, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type=f"manufacturing.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


# ---- the hold place ----

def _entity_id(entity_id):
    if entity_id:
        return entity_id
    from entities.services import main_entity

    return main_entity().pk


def hold_code(entity_id):
    return f"{HOLD_PREFIX}{_entity_id(entity_id)}"


def hold_place(entity_id, create=True):
    """The entity's quality hold place, made the first time it is needed. Never a selling location: if someone ticked
    «مكان بيع» on it from the locations screen, it is switched back here before anything new is put on hold."""

    code = hold_code(entity_id)
    if not create:
        return Location.objects.filter(location_code=code).first()
    place, _ = Location.objects.get_or_create(location_code=code, defaults={
        "name_ar": MESSAGES["ar"]["hold_name"], "name_en": MESSAGES["en"]["hold_name"], "entity_id": _entity_id(entity_id),
        "is_default": False, "is_receiving_location": True, "is_selling_location": False,
        "description": "HG-136: goods waiting for a quality check. Not a selling location."})
    if place.is_selling_location:
        place.is_selling_location = False
        place.save(update_fields=["is_selling_location", "updated_at"])
    return place


def is_hold_place(location):
    return location is not None and location.location_code == f"{HOLD_PREFIX}{location.entity_id}"


def hold_place_for(order):
    """Where an order's product enters stock at the finish when its recipe needs release; None for every other order."""

    return hold_place(order.location.entity_id) if order.recipe.needs_release else None


def current_hold_place(create=False):
    """The hold place of the entity being worked in (the main entity when the whole group is shown)."""

    chosen = entity_scope.entity()
    return hold_place(chosen.pk if chosen is not None else None, create=create)


def hold_places():
    """The hold places of the entity being worked in (every entity's when the whole group is shown)."""

    return [place for place in entity_scope.locations(Location.objects.filter(location_code__startswith=HOLD_PREFIX)) if is_hold_place(place)]


def _own_code(prefix):
    """The code ``<prefix><the location's own entity id>``, as an expression: exactly the system's place, never a
    warehouse the owner happened to name «QC-...» himself."""

    return Concat(Value(prefix), Cast("entity_id", CharField()))


def production_places(queryset):
    """Locations a product can be made in: never a hold place."""

    return queryset.exclude(location_code=_own_code(HOLD_PREFIX))


def destinations(place):
    """Where an incoming release may go: the active locations of the hold place's entity, less the hold and transit places."""

    return (production_places(Location.objects.filter(active=True, entity_id=place.entity_id))
            .exclude(location_code=_own_code("TRANSIT-")).exclude(location_code="TRANSIT-0").order_by("-is_default", "location_code"))


def check_plain_run(recipe, lang="ar"):
    """A plain run (no order) of a recipe that needs release would put the product straight on the shelf: refused."""

    if recipe.needs_release:
        raise ValidationError(_words(lang)["plain_run"])


# ---- what waits ----

@dataclass
class Subject:
    """What a check decides on: a finished order's goods, or a purchase line, waiting in a hold place."""

    kind: str
    item: object
    place: Location
    made: Decimal
    order: object = None
    line: object = None

    @property
    def home(self):
        return self.order.location if self.order is not None else None

    def checks(self):
        owner = {"order": self.order} if self.order is not None else {"purchase_line": self.line}
        return QualityCheck.objects.filter(kind=self.kind, **owner)

    def criteria(self):
        if self.order is not None:
            return list(self.order.recipe.criteria.all())
        return list(QualityCriterion.objects.filter(item=self.item))


def order_subject(order):
    """The goods of a finished order if they entered a hold place (and its run still stands), else None."""

    run = order.run
    if run is None or run.status != RunStatus.POSTED:
        return None
    place = run.output_operation.destination_location
    if not is_hold_place(place):
        return None
    return Subject(CheckKind.FINAL, order.recipe.product, place, run.output_quantity, order=order)


def purchase_subject(line):
    """A stock-tracked line of a posted purchase invoice received into a hold place, else None."""

    invoice = line.invoice
    if invoice.status != POSTED or not line.item.is_stock_tracked or not is_hold_place(invoice.receiving_location):
        return None
    return Subject(CheckKind.INCOMING, line.item, invoice.receiving_location, line.quantity, line=line)


def _returned(line):
    """What a purchase line has sent back to the supplier on posted returns."""

    from purchases.models import PurchaseReturnLine

    return PurchaseReturnLine.objects.filter(source_line=line, purchase_return__status=POSTED).aggregate(total=Sum("quantity"))["total"] or ZERO


def position(subject):
    """{"made", "passed", "rejected", "waiting", "to_return"} for what a subject put on hold.

    Passed counts while its release is posted; a finished-goods reject counts while its write-off is posted; an
    incoming reject is a record (the goods stay in the hold place until the purchase return). ``waiting`` is never more
    than the hold place really holds of the item (less the incoming rejects still there)."""

    from inventory.services import get_item_location_stock_quantity

    passed = rejected = ZERO
    for check in subject.checks().select_related("release_operation", "reject_operation"):
        if check.release_operation_id and check.release_operation.status == POSTED:
            passed += check.passed_quantity
        if subject.kind == CheckKind.INCOMING or (check.reject_operation_id and check.reject_operation.status == POSTED):
            rejected += check.rejected_quantity
    to_return = max(rejected - _returned(subject.line), ZERO) if subject.kind == CheckKind.INCOMING else ZERO
    there = get_item_location_stock_quantity(subject.item, subject.place) - to_return
    waiting = max(min(subject.made - passed - rejected, there), ZERO)
    return {"made": subject.made, "passed": passed, "rejected": rejected, "waiting": waiting, "to_return": to_return}


def _held_items(places):
    """Ids of the items a hold place holds anything of."""

    from inventory.models import StockMovement
    from inventory.services import IN_MOVEMENT_TYPES, OUT_MOVEMENT_TYPES

    totals = {}
    rows = StockMovement.objects.filter(location__in=places).values("item_id", "movement_type").annotate(total=Sum("quantity"))
    for row in rows:
        sign = 1 if row["movement_type"] in IN_MOVEMENT_TYPES else -1 if row["movement_type"] in OUT_MOVEMENT_TYPES else 0
        totals[row["item_id"]] = totals.get(row["item_id"], ZERO) + sign * (row["total"] or ZERO)
    return [item_id for item_id, total in totals.items() if total > 0]


def scoped_orders():
    return entity_scope.scope(ProductionOrder.objects, "location__entity")


def scoped_lines():
    from purchases.models import PurchaseLine

    return entity_scope.scope(PurchaseLine.objects, "invoice__receiving_location__entity")


def scoped_checks():
    """The checks of the entity being worked in: an order's through its location, a purchase's through its receiving location."""

    if not entity_scope.is_scoped():
        return QualityCheck.objects.all()
    return QualityCheck.objects.filter((Q(order__isnull=False) & entity_scope.q("order__location__entity"))
                                       | (Q(purchase_line__isnull=False) & entity_scope.q("purchase_line__invoice__receiving_location__entity")))


def product_lot(subject):
    """The lot (``batches.Batch``) of what waits: the run's product lot, or the purchase line's batch; None when there is none."""

    from batches.models import Batch

    if subject.order is not None:
        return Batch.objects.filter(production_run=subject.order.run).first()
    return Batch.objects.filter(purchase_line=subject.line).first()


def waiting_rows():
    """What waits for a check in the entity being worked in: [{"subject", "position", "lot"}], finished orders first."""

    places = hold_places()
    if not places:
        return []
    items = _held_items(places)
    if not items:
        return []
    rows = []
    held_orders = (scoped_orders().filter(status=OrderStatus.DONE, run__status=RunStatus.POSTED, recipe__product_id__in=items,
                                          run__output_operation__destination_location__in=places)
                   .select_related("recipe__product", "location", "run__output_operation__destination_location").order_by("finished_at", "pk"))
    for order in held_orders:
        subject = order_subject(order)
        if subject is None:
            continue
        where = position(subject)
        if where["waiting"] > 0:
            rows.append({"subject": subject, "position": where, "lot": product_lot(subject)})
    lines = (scoped_lines().filter(invoice__status=POSTED, invoice__receiving_location__in=places, item_id__in=items, item__is_stock_tracked=True)
             .select_related("invoice__receiving_location", "invoice__supplier", "item").order_by("invoice__invoice_date", "pk"))
    for line in lines:
        subject = purchase_subject(line)
        if subject is None:
            continue
        where = position(subject)
        if where["waiting"] > 0:
            rows.append({"subject": subject, "position": where, "lot": product_lot(subject)})
    return rows


def return_rows():
    """Incoming rejects still in the hold place, waiting for their purchase return: [{"line", "quantity"}]."""

    places = hold_places()
    if not places:
        return []
    lines = (scoped_lines().filter(invoice__receiving_location__in=places, quality_checks__rejected_quantity__gt=0).distinct()
             .select_related("invoice__supplier", "item").order_by("invoice__invoice_date", "pk"))
    rows = []
    for line in lines:
        subject = purchase_subject(line)
        if subject is None:
            continue
        left = position(subject)["to_return"]
        if left > 0:
            rows.append({"line": line, "quantity": left})
    return rows


# ---- criteria ----

def _limit(raw):
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None, True
    number = services._decimal(text)
    return number, number is not None


def clean_criteria(rows, lang="ar"):
    """[{name_ar, name_en, kind, min_value, max_value}] from the form's rows; an empty row is skipped."""

    words = _words(lang)
    clean = []
    for index, row in enumerate(rows, start=1):
        name_ar, name_en = (row.get("name_ar") or "").strip()[:120], (row.get("name_en") or "").strip()[:120]
        low_raw, high_raw = row.get("min_value"), row.get("max_value")
        if not name_ar and not name_en and not str(low_raw or "").strip() and not str(high_raw or "").strip():
            continue
        if not name_ar:
            raise ValidationError(words["criterion_name"].format(row=index))
        kind = row.get("kind") or CriterionKind.PASS_FAIL
        if kind not in CriterionKind.values:
            raise ValidationError(words["criterion_kind"].format(row=index))
        low, high = None, None
        if kind == CriterionKind.NUMBER:
            (low, low_ok), (high, high_ok) = _limit(low_raw), _limit(high_raw)
            if not low_ok or not high_ok or (low is not None and high is not None and low > high):
                raise ValidationError(words["criterion_limit"].format(name=name_ar))
        clean.append({"name_ar": name_ar, "name_en": name_en, "kind": kind, "min_value": low, "max_value": high})
    return clean


def _replace_criteria(owner_field, owner, rows, lang):
    clean = clean_criteria(rows, lang)
    QualityCriterion.objects.filter(**{owner_field: owner}).delete()
    QualityCriterion.objects.bulk_create([QualityCriterion(order=index, **{owner_field: owner}, **row) for index, row in enumerate(clean, start=1)])
    return clean


def _may_edit_criteria(user, lang):
    if not user_has_permission(user, CRITERIA):
        raise PermissionDenied(_words(lang)["criteria_permission"])


@transaction.atomic
def save_recipe_quality(recipe, user, *, needs_release, rows, lang="ar"):
    """The recipe page's quality section: whether its orders finish on hold, and its criteria (the whole list is replaced)."""

    _may_edit_criteria(user, lang)
    clean = _replace_criteria("recipe", recipe, rows, lang)
    recipe.needs_release = bool(needs_release)
    recipe.save(update_fields=["needs_release"])
    _audit(recipe, user, "save_recipe_quality", {"needs_release": recipe.needs_release, "criteria": [row["name_ar"] for row in clean]})
    return recipe


@transaction.atomic
def save_item_criteria(item, user, *, rows, lang="ar"):
    """The criteria of a bought item's incoming check (the whole list is replaced)."""

    _may_edit_criteria(user, lang)
    clean = _replace_criteria("item", item, rows, lang)
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action="save_item_criteria",
                            object_type="master_data.Item", object_id=str(item.pk), before_data={},
                            after_data={"criteria": [row["name_ar"] for row in clean]})
    return clean


def criteria_form_rows(criteria, posted=None):
    """CRITERIA_ROWS rows for the criteria form: what was posted (after a refusal), else the saved list, padded with blanks."""

    if posted is not None:
        rows = list(posted)
    else:
        rows = [{"name_ar": c.name_ar, "name_en": c.name_en, "kind": c.kind, "min_value": plain(c.min_value), "max_value": plain(c.max_value)} for c in criteria]
    blank = {"name_ar": "", "name_en": "", "kind": CriterionKind.PASS_FAIL, "min_value": "", "max_value": ""}
    return (rows + [dict(blank) for _ in range(CRITERIA_ROWS)])[:max(CRITERIA_ROWS, len(rows))]


def plain(number):
    """A number as a form field takes it back: no thousands separator (a comma is read as a decimal point), no trailing zeros."""

    return "" if number is None else f"{number.normalize():f}"


def limits_text(row, lang="ar"):
    """«من 2 إلى 8» for a number criterion (or a result's copy of it); empty for pass/fail or no limits."""

    if row.kind != CriterionKind.NUMBER or (row.min_value is None and row.max_value is None):
        return ""
    low, high = plain(row.min_value), plain(row.max_value)
    if lang == "en":
        return f"{low} to {high}" if low and high else (f"at least {low}" if low else f"at most {high}")
    return f"من {low} إلى {high}" if low and high else (f"{low} على الأقل" if low else f"{high} على الأكتر")


def read_results(criteria, raw, lang="ar"):
    """[(criterion, value, ok)] for the criteria that were answered. ``raw`` is {criterion pk: "pass" | "fail" | a number}."""

    words = _words(lang)
    rows = []
    for criterion in criteria:
        text = str(raw.get(criterion.pk, "") or "").strip()
        if not text:
            continue
        if criterion.kind == CriterionKind.NUMBER:
            value = services._decimal(text)
            if value is None:
                raise ValidationError(words["number"].format(name=criterion.name(lang)))
            ok = (criterion.min_value is None or value >= criterion.min_value) and (criterion.max_value is None or value <= criterion.max_value)
            rows.append((criterion, value, ok))
        else:
            if text not in ("pass", "fail"):
                raise ValidationError(words["answer"].format(name=criterion.name(lang)))
            rows.append((criterion, None, text == "pass"))
    return rows


def _write_results(check, rows):
    QualityResult.objects.bulk_create([QualityResult(quality_check=check, criterion=criterion, name_ar=criterion.name_ar, name_en=criterion.name_en,
                                                     kind=criterion.kind, min_value=criterion.min_value, max_value=criterion.max_value, value=value, ok=ok)
                                       for criterion, value, ok in rows])


# ---- a check on what waits ----

def _quantity(raw, words):
    if raw in (None, ""):
        return ZERO
    number = services._decimal(raw)
    if number is None or number < 0:
        raise ValidationError(words["quantity"])
    return number


def _carry_lot(subject, user):
    """Plan trap 14: the lot follows the goods. It sits where the latest release that still stands sent them, else in the hold place."""

    lot = product_lot(subject)
    if lot is None:
        return
    released = (subject.checks().filter(release_operation__status=POSTED, destination__isnull=False).order_by("-pk").select_related("destination").first())
    target = released.destination if released is not None else subject.place
    if lot.location_id != target.pk:
        before = lot.location.location_code
        lot.location = target
        lot.save(update_fields=["location"])
        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="manufacturing", action="move_lot_with_release", object_type="batches.Batch",
                                object_id=str(lot.pk), before_data={"location": before}, after_data={"location": target.location_code})


def _decide(subject, user, *, passed, rejected, note, results, destination, lang):
    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock, lock_items_for_stock_check, transfer_stock

    words = _words(lang)
    if not user_has_permission(user, RELEASE):
        raise PermissionDenied(words["release_permission"])
    if subject is None:
        raise ValidationError(words["nothing"])
    lock_items_for_stock_check([subject.item.pk])  # nothing of it moves between reading what waits and moving it
    waiting = position(subject)["waiting"]
    if waiting <= 0:
        raise ValidationError(words["nothing"])
    passed, rejected = _quantity(passed, words), _quantity(rejected, words)
    if passed + rejected > waiting:
        raise ValidationError(words["over"].format(waiting=fmt_qty(waiting)))
    note = (note or "").strip()[:255]
    criteria = subject.criteria()
    rows = read_results(criteria, results or {}, lang)
    if passed > 0:
        if len(rows) < len(criteria):
            raise ValidationError(words["answer_all"])
        if any(not ok for _criterion, _value, ok in rows) and not note:
            raise ValidationError(words["failed_note"])
    if passed == 0 and rejected == 0 and not note:
        raise ValidationError(words["hold_note"])
    final = subject.kind == CheckKind.FINAL
    if rejected > 0 and final and not user_has_permission(user, REJECT):
        raise ValidationError(words["reject_permission"])
    if passed > 0:
        if final:
            destination = subject.home
        elif destination is None or not destinations(subject.place).filter(pk=destination.pk).exists():
            raise ValidationError(words["destination"])
    else:
        destination = None
    result = (CheckResult.HELD if passed == 0 and rejected == 0 else CheckResult.PASSED if passed == waiting
              else CheckResult.REJECTED if rejected == waiting else CheckResult.PARTLY)
    day = timezone.localdate()
    number = services._next(QualityCheck, "number", f"QC-{day:%Y%m%d}-", 3)
    check = QualityCheck.objects.create(number=number, kind=subject.kind, order=subject.order, purchase_line=subject.line, checked_on=day, result=result,
                                        quantity=waiting, passed_quantity=passed, rejected_quantity=rejected, note=note, destination=destination, checked_by=user)
    _write_results(check, rows)
    if passed > 0:
        check.release_operation = transfer_stock(f"{number}-REL", day, subject.item, subject.place, destination, passed, user,
                                                 reason=f"إفراج جودة {number} / Quality release {number}")
    if rejected > 0 and final:
        reason = f"مرفوض في فحص الجودة {number}{': ' + note if note else ''} / Rejected at quality check {number}"
        check.reject_operation = adjust_stock(f"{number}-REJ", day, subject.item, subject.place, StockAdjustmentDirection.OUT, rejected, reason, user)
    if check.release_operation_id or check.reject_operation_id:
        check.save(update_fields=["release_operation", "reject_operation"])
    _carry_lot(subject, user)
    _audit(check, user, "quality_check", {"number": number, "kind": subject.kind, "result": result, "waiting": str(waiting), "passed": str(passed),
                                          "rejected": str(rejected), "failed": [criterion.name_ar for criterion, _value, ok in rows if not ok]},
           AuditEventType.CREATE)
    return check


@transaction.atomic
def check_order(order, user, *, passed="", rejected="", note="", results=None, lang="ar"):
    """Record a check on a finished order's goods on hold: release ``passed``, write off ``rejected``, keep the rest on hold."""

    order = ProductionOrder.objects.select_for_update().select_related("recipe__product", "location", "run__output_operation__destination_location").get(pk=order.pk)
    return _decide(order_subject(order), user, passed=passed, rejected=rejected, note=note, results=results, destination=None, lang=lang)


@transaction.atomic
def check_purchase_line(line, user, *, passed="", rejected="", destination=None, note="", results=None, lang="ar"):
    """Record an incoming check on a purchase line received into the hold place: release ``passed`` to ``destination``;
    ``rejected`` stays in the hold place for the purchase return."""

    from purchases.models import PurchaseLine

    line = PurchaseLine.objects.select_for_update().select_related("invoice__receiving_location", "item").get(pk=line.pk)
    return _decide(purchase_subject(line), user, passed=passed, rejected=rejected, note=note, results=results, destination=destination, lang=lang)


# ---- in process: the order's quality stage ----

def stage_criteria(order):
    """The recipe's criteria when the order's current stage is its quality stage, else []."""

    if order.status not in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS) or order.stage_index >= len(order.stages):
        return []
    if order.stages[order.stage_index][0] != QUALITY_STAGE:
        return []
    return list(order.recipe.criteria.all())


@transaction.atomic
def advance(order, user, *, results=None, good=None, defect=None, note="", minutes=None, lang="ar"):
    """``orders.advance`` for the quality stage of an order whose recipe has criteria: every criterion is recorded with
    the stage, and a failed one needs a note. Nothing else changes: failed units are the stage's defects, as before.
    Any other stage (or a recipe with no criteria) goes straight to ``orders.advance``."""

    from . import orders

    words = _words(lang)
    order = ProductionOrder.objects.select_for_update().select_related("recipe").get(pk=order.pk)
    criteria = stage_criteria(order)
    if not criteria:
        return orders.advance(order, user, good=good, defect=defect, note=note, minutes=minutes, lang=lang)
    rows = read_results(criteria, results or {}, lang)
    if len(rows) < len(criteria):
        raise ValidationError(words["answer_stage"])
    failed = [criterion for criterion, _value, ok in rows if not ok]
    if failed and not (note or "").strip():
        raise ValidationError(words["failed_note"])
    order = orders.advance(order, user, good=good, defect=defect, note=note, minutes=minutes, lang=lang)
    log = order.stage_logs.order_by("-pk").first()
    if log is None or log.stage_key != QUALITY_STAGE:
        raise ValidationError(words["state"])
    result = (CheckResult.REJECTED if log.good_quantity == 0 else CheckResult.PASSED if log.defect_quantity == 0 and not failed else CheckResult.PARTLY)
    day = timezone.localdate()
    check = QualityCheck.objects.create(number=services._next(QualityCheck, "number", f"QC-{day:%Y%m%d}-", 3), kind=CheckKind.STAGE, order=order, stage_log=log,
                                        checked_on=day, result=result, quantity=log.good_quantity + log.defect_quantity, passed_quantity=log.good_quantity,
                                        rejected_quantity=log.defect_quantity, note=(note or "").strip()[:255], checked_by=user)
    _write_results(check, rows)
    _audit(check, user, "quality_stage_check", {"number": check.number, "order": order.number, "result": result,
                                                "failed": [criterion.name_ar for criterion in failed]}, AuditEventType.CREATE)
    return order


# ---- what the pages read ----

def operation_state(check):
    """{"released": bool, "release_reversed": bool, "written_off": bool, "reject_reversed": bool} from the check's own operations."""

    release, reject = check.release_operation, check.reject_operation
    return {"released": release is not None and release.status == POSTED, "release_reversed": release is not None and release.status != POSTED,
            "written_off": reject is not None and reject.status == POSTED, "reject_reversed": reject is not None and reject.status != POSTED}


def order_card(order):
    """The order page's quality card: None for an order that has nothing to do with quality; else its hold position and checks."""

    checks = list(order.quality_checks.select_related("checked_by", "release_operation", "reject_operation").order_by("pk"))
    subject = order_subject(order) if order.run_id else None
    if subject is None and not checks and not order.recipe.needs_release:
        return None
    return {"subject": subject, "position": position(subject) if subject is not None else None, "checks": checks,
            "lot": product_lot(subject) if subject is not None else None,
            "will_hold": order.recipe.needs_release and order.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)}
