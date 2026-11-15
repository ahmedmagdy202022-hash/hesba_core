"""Restaurants R8 (HG-118): table reservations («الحجوزات»).

Rules, stated once:

* a reservation belongs to one branch (entity): the table's when it has one, else the branch it was made in; a branch's screens
  only ever list that branch's reservations, and a table of another branch is refused;
* **no two live reservations on one table overlap** (booked, confirmed or seated). A reservation holds its table from
  ``starts_at`` for ``duration_minutes``; one that ends exactly when the next starts does not clash. The check runs under the
  table's row lock (the lock ``open_order`` and ``save_table`` take), so two people booking the same table at once are
  serialised on PostgreSQL as on SQLite;
* a party bigger than the table's seats is refused with a warning, unless the user says to go on (``override``);
* «وصل» (seat) opens the table's restaurant order through ``services.open_order``, with the party as its guests and the
  reservation's customer: no new order path. It refuses when the table already has an open order (``open_order`` itself would
  join that order silently);
* confirm, cancel and no-show are status changes, audited. Cancel and no-show run the S5 cancellation policy on a held deposit
  (``deposits.services``, unchanged): late (inside the policy's hours, or a no-show) forfeits it into a «رسوم إلغاء» invoice,
  early refunds it through HG-068's gate;
* a reservation that is seated, cancelled or a no-show is never edited again;
* the public page («/book-table/<key>/») only ever makes a ``booked`` request with no table and no customer, for staff to
  confirm; it shows nothing back;
* the held deposit of a seated reservation settles the table's bill when it is paid (``settle_at_bill``, called by
  ``services.pay``): ``deposits.services.attach`` then ``apply`` on the posted invoice, as S5 applies it on posting.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from entities import scope as entity_scope
from entities.current import current_entity, working_in
from entities.services import main_entity
from permissions.services import user_has_permission

from .models import LIVE_RESERVATION_STATUSES, DiningTable, Order, OrderStatus, ReservationPage, ReservationStatus, TableReservation

DEFAULT_DURATION = 90
MIN_DURATION = 15
MAX_DURATION = 720
MAX_PARTY = 99
PUBLIC_DAYS_AHEAD = 60
NUMBER_ATTEMPTS = 5
OPEN = (ReservationStatus.BOOKED, ReservationStatus.CONFIRMED)

MESSAGES = {
    "ar": {
        "name": "اكتب اسم الضيف.", "phone": "اكتب رقم موبايل صحيح.", "party": "عدد الأفراد لازم من 1 لـ {max}.",
        "when": "اختار يوم وساعة الحجز.", "past": "الميعاد ده فات؛ اختار ميعاد جاي.", "far": "الحجز من هنا لحد {days} يوم قدام بس.",
        "duration": "مدة الحجز لازم من {min} دقيقة لحد {max} دقيقة.",
        "table_branch": "الطاولة دي تبع فرع تاني.", "table_off": "الطاولة دي مش شغالة.", "table_needed": "اختار الطاولة الأول.",
        "clash": "الطاولة {table} محجوزة في الوقت ده ({number} من {start} لـ {end}).",
        "seats": "عدد الأفراد ({party}) أكبر من كراسي الطاولة {table} ({seats}). لو متأكد، علّم «كمّل رغم العدد» واحفظ تاني.",
        "busy": "الطاولة {table} عليها طلب مفتوح؛ اقفله أو اختار طاولة تانية.",
        "state": "الحجز ده حالته «{status}»؛ مينفعش يتعدّل.", "not_booked": "التأكيد للحجز اللي لسه محجوز بس.",
        "customer_locked": "الحجز عليه عربون؛ العميل مينفعش يتغيّر.", "foreign": "الحجز ده مش من حجوزات الفرع اللي شغال فيه.",
        "no_customer": "العربون محتاج عميل باسمه على الحجز؛ اختار العميل الأول.", "cashbox": "اختار خزنة من خزن فرع الحجز.",
        "bad_status": "اختار إلغاء أو ما حضرش.", "deposit_mismatch": "العربون ما اتخصمش من الحساب زي ما اتحسب؛ ما اتسجلش حاجة. جرّب تاني.",
        "order_note": "حجز {number}",
        "statuses": {"booked": "محجوز", "confirmed": "مؤكد", "seated": "وصل", "cancelled": "اتلغى", "no_show": "ما حضرش"},
    },
    "en": {
        "name": "Enter the guest's name.", "phone": "Enter a valid mobile number.", "party": "The party must be 1 to {max} guests.",
        "when": "Choose the day and time of the booking.", "past": "That time has passed; choose a time ahead.", "far": "Bookings here go up to {days} days ahead only.",
        "duration": "The booking must last {min} to {max} minutes.",
        "table_branch": "This table belongs to another branch.", "table_off": "This table is not active.", "table_needed": "Choose the table first.",
        "clash": "Table {table} is booked at that time ({number}, {start} to {end}).",
        "seats": "The party ({party}) is bigger than table {table}'s seats ({seats}). If you are sure, tick “Go on despite the party size” and save again.",
        "busy": "Table {table} has an open order; close it or choose another table.",
        "state": "This booking is “{status}”; it cannot be changed.", "not_booked": "Only a booking that is still booked can be confirmed.",
        "customer_locked": "This booking holds a deposit; its customer cannot change.", "foreign": "This booking is not one of the branch you are working in.",
        "no_customer": "A deposit needs a named customer on the booking; choose the customer first.", "cashbox": "Choose one of the booking's branch's cashboxes.",
        "bad_status": "Choose cancel or no-show.", "deposit_mismatch": "The deposit did not settle the bill as worked out; nothing was kept. Try again.",
        "order_note": "Booking {number}",
        "statuses": {"booked": "Booked", "confirmed": "Confirmed", "seated": "Seated", "cancelled": "Cancelled", "no_show": "No-show"},
    },
}


def status_label(reservation, lang="ar"):
    return MESSAGES[lang]["statuses"].get(reservation.status, reservation.status)


def _audit(reservation, user, action, after, before=None, event=AuditEventType.UPDATE, reason=""):
    AuditLog.objects.create(event_type=event, actor=user, module="restaurant", action=action, object_type="restaurant.TableReservation",
                            object_id=str(reservation.pk), before_data=before or {}, after_data=after, reason=reason)


def _snapshot(reservation):
    return {"status": reservation.status, "table": reservation.table_id, "customer": reservation.customer_id, "name": reservation.name, "phone": reservation.phone,
            "party_size": reservation.party_size, "starts_at": reservation.starts_at.isoformat() if reservation.starts_at else "",
            "duration_minutes": reservation.duration_minutes, "notes": reservation.notes}


# --- reading -----------------------------------------------------------------

def scoped(queryset=None):
    """The reservations of the entity being worked in (the whole group sees every branch's)."""

    return entity_scope.scope(queryset if queryset is not None else TableReservation.objects.all(), "entity")


def day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    return start, start + timedelta(days=1)


def for_day(day):
    """One day's reservations, by time: what the day view lists."""

    start, end = day_bounds(day)
    return scoped(TableReservation.objects.filter(starts_at__gte=start, starts_at__lt=end)).select_related("table", "customer", "order", "entity")


def local_time(value):
    return timezone.localtime(value).strftime("%H:%M")


def cashboxes(reservation):
    """The cashboxes a deposit on this reservation may go into: its branch's, active, never a held kind."""

    from cashboxes.models import Cashbox

    with working_in(reservation.entity):
        return entity_scope.cashboxes(Cashbox.objects).filter(active=True)


# --- cleaning ----------------------------------------------------------------

def _int(raw, default=None):
    try:
        return int(str(raw).strip()) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return None


def _when(data, words):
    """``starts_at`` as given (an aware datetime), or from the form's ``day`` (YYYY-MM-DD) and ``time`` (HH:MM), local time."""

    value = data.get("starts_at")
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    day, clock = str(data.get("day") or "").strip(), str(data.get("time") or "").strip()
    try:
        parsed = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValidationError(words["when"])
    return timezone.make_aware(parsed)


def _phone(raw, words, required=False):
    from .callcentre import latin_digits

    phone = latin_digits(str(raw or "")).strip()[:50]
    digits = "".join(ch for ch in phone if ch.isdigit())
    if (required or phone) and not 7 <= len(digits) <= 15:
        raise ValidationError(words["phone"])
    return phone


def clean(data, words, public=False):
    """The fields a reservation is made of, checked. ``public``: the guest's own request (phone required, a time ahead)."""

    name = (data.get("name") or "").strip()[:120]
    if not name:
        raise ValidationError(words["name"])
    party = _int(data.get("party_size"), 2)
    if party is None or not 1 <= party <= MAX_PARTY:
        raise ValidationError(words["party"].format(max=MAX_PARTY))
    duration = DEFAULT_DURATION if public else _int(data.get("duration_minutes"), DEFAULT_DURATION)
    if duration is None or not MIN_DURATION <= duration <= MAX_DURATION:
        raise ValidationError(words["duration"].format(min=MIN_DURATION, max=MAX_DURATION))
    starts_at = _when(data, words)
    if public:
        now = timezone.now()
        if starts_at < now:
            raise ValidationError(words["past"])
        if starts_at > now + timedelta(days=PUBLIC_DAYS_AHEAD):
            raise ValidationError(words["far"].format(days=PUBLIC_DAYS_AHEAD))
    return {"name": name, "phone": _phone(data.get("phone"), words, required=public), "party_size": party, "starts_at": starts_at,
            "duration_minutes": duration, "notes": (data.get("notes") or "").strip()[:255]}


# --- the table: lock, clash, seats ---------------------------------------------

def _locked_table(table, entity, words):
    """The table, locked (``open_order``'s lock), checked to be an active table of ``entity``."""

    table = DiningTable.objects.select_for_update().get(pk=table.pk)
    if table.entity_id != entity.pk:
        raise ValidationError(words["table_branch"])
    if not table.active:
        raise ValidationError(words["table_off"])
    return table


def clash(table, starts_at, duration, exclude=None):
    """The first live reservation on ``table`` whose time overlaps [starts_at, starts_at + duration), or None.

    The database narrows to the reservations that start before this one ends and not more than the longest booking before it
    starts; the exact overlap (``a.start < b.end and b.start < a.end``) is checked here, the same on SQLite and PostgreSQL."""

    ends_at = starts_at + timedelta(minutes=duration)
    rows = TableReservation.objects.filter(table=table, status__in=LIVE_RESERVATION_STATUSES, starts_at__lt=ends_at,
                                           starts_at__gt=starts_at - timedelta(minutes=MAX_DURATION)).order_by("starts_at", "pk")
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    for row in rows:
        if row.starts_at < ends_at and starts_at < row.ends_at:
            return row
    return None


def _check_table(table, entity, values, words, exclude=None, override=False):
    table = _locked_table(table, entity, words)
    found = clash(table, values["starts_at"], values["duration_minutes"], exclude)
    if found is not None:
        raise ValidationError(words["clash"].format(table=table.name, number=found.number, start=local_time(found.starts_at), end=local_time(found.ends_at)),
                              code="clash")
    if values["party_size"] > table.seats and not override:
        raise ValidationError(words["seats"].format(party=values["party_size"], table=table.name, seats=table.seats), code="seats")
    return table


# --- making and changing ---------------------------------------------------------

def _target(reservation, entity, words):
    working = current_entity()
    target = reservation.entity if reservation is not None else (entity or working or main_entity())
    if working is not None and target.pk != working.pk:
        raise ValidationError(words["foreign"])
    return target


def _table_of(raw, entity, words):
    """A table by instance or pk, of ``entity`` only (another branch's is refused, not ignored)."""

    if raw in (None, ""):
        return None
    if isinstance(raw, DiningTable):
        table = raw
    else:
        table = DiningTable.objects.filter(pk=raw).first() if str(raw).strip().isdigit() else None
        if table is None:
            raise ValidationError(words["table_branch"])
    if table.entity_id != entity.pk:
        raise ValidationError(words["table_branch"])
    return table


def _customer_of(raw):
    from master_data.models import Customer
    from sales.pos import WALK_IN_CODE

    if raw in (None, ""):
        return None
    customer = raw if isinstance(raw, Customer) else (Customer.objects.filter(pk=raw, active=True).first() if str(raw).strip().isdigit() else None)
    if customer is None or customer.customer_code == WALK_IN_CODE:
        return None  # the walk-in is no one: the reservation keeps its name and phone
    return customer


def _new_number(day):
    from config.numbering import next_in_series

    return next_in_series(TableReservation, "number", f"RES-{day:%Y%m%d}-", width=3)


def _create(values, **extra):
    """A new reservation under a fresh number: two made in the same instant can compute the same one; the second's savepoint
    rolls back on the UNIQUE ``number`` and it takes the next."""

    for attempt in range(NUMBER_ATTEMPTS):
        try:
            with transaction.atomic():
                return TableReservation.objects.create(number=_new_number(timezone.localdate()), **values, **extra)
        except IntegrityError:
            if attempt == NUMBER_ATTEMPTS - 1:
                raise


def _locked(reservation):
    # ``of=self``: PostgreSQL refuses FOR UPDATE on the nullable side of an outer join (table, customer, order).
    return TableReservation.objects.select_for_update(of=("self",)).select_related("table", "customer", "entity").get(pk=reservation.pk)


@transaction.atomic
def save_reservation(data, user, reservation=None, lang="ar", entity=None, override=False):
    """Make or change a reservation. ``data``: name, phone, party_size, starts_at (or day and time), duration_minutes, notes,
    table (instance or pk, optional) and customer (instance or pk, optional). A new one lands in ``entity`` (else the working
    entity, else the main one); it never changes branch afterwards. ``override``: go on although the party is bigger than the
    table's seats."""

    words = MESSAGES[lang]
    if reservation is not None:
        reservation = _locked(reservation)
        if reservation.status not in OPEN:
            raise ValidationError(words["state"].format(status=status_label(reservation, lang)))
    target = _target(reservation, entity, words)
    values = clean(data, words)
    table = _table_of(data.get("table"), target, words)
    customer = _customer_of(data.get("customer"))
    if reservation is not None and customer != reservation.customer and reservation.customer_id is not None:
        from deposits import services as deposits

        if deposits.held_for(reservation) is not None:
            raise ValidationError(words["customer_locked"])
    if table is not None:
        table = _check_table(table, target, values, words, exclude=getattr(reservation, "pk", None), override=override)
    values.update(table=table, customer=customer)
    if reservation is None:
        reservation = _create(values, entity=target, created_by=user)
        _audit(reservation, user, "create_reservation", {**_snapshot(reservation), "number": reservation.number, "entity": target.pk,
                                                          **({"over_seats": True} if override and table and values["party_size"] > table.seats else {})},
               event=AuditEventType.CREATE)
        return reservation
    before = _snapshot(reservation)
    for key, value in values.items():
        setattr(reservation, key, value)
    reservation.save()
    _audit(reservation, user, "change_reservation", {**_snapshot(reservation), **({"over_seats": True} if override and table and values["party_size"] > table.seats else {})},
           before)
    return reservation


@transaction.atomic
def confirm(reservation, user, lang="ar"):
    """Staff confirm a booking (a request from the public page, or a phone booking): booked → confirmed."""

    words = MESSAGES[lang]
    reservation = _locked(reservation)
    if reservation.status != ReservationStatus.BOOKED:
        raise ValidationError(words["not_booked"])
    reservation.status = ReservationStatus.CONFIRMED
    reservation.save(update_fields=["status", "updated_at"])
    _audit(reservation, user, "confirm_reservation", {"status": reservation.status}, {"status": ReservationStatus.BOOKED})
    return reservation


@transaction.atomic
def seat(reservation, user, table=None, override=False, lang="ar"):
    """«وصل»: the guests arrived. Opens the table's restaurant order with ``services.open_order`` (the party as its guests, the
    reservation's customer), and links it. ``table`` assigns or changes the table on the way; the clash and seats rules apply.
    Refused when the table already has an open order."""

    from . import services

    words = MESSAGES[lang]
    reservation = _locked(reservation)
    if reservation.status not in OPEN:
        raise ValidationError(words["state"].format(status=status_label(reservation, lang)))
    entity = _target(reservation, None, words)
    table = _table_of(table, entity, words) if table not in (None, "") else reservation.table
    if table is None:
        raise ValidationError(words["table_needed"])
    values = {"starts_at": reservation.starts_at, "duration_minutes": reservation.duration_minutes, "party_size": reservation.party_size}
    table = _check_table(table, entity, values, words, exclude=reservation.pk, override=override)
    if Order.objects.filter(table=table, status=OrderStatus.OPEN).exists():
        raise ValidationError(words["busy"].format(table=table.name))
    order = services.open_order(user, table=table, guests=reservation.party_size, customer=reservation.customer, lang=lang,
                                phone=reservation.phone, notes=words["order_note"].format(number=reservation.number))
    before = {"status": reservation.status, "table": reservation.table_id}
    reservation.status, reservation.table, reservation.order = ReservationStatus.SEATED, table, order
    reservation.save(update_fields=["status", "table", "order", "updated_at"])
    _audit(reservation, user, "seat_reservation", {"status": reservation.status, "table": table.pk, "order": order.number,
                                                    **({"over_seats": True} if override and reservation.party_size > table.seats else {})}, before)
    return reservation


@transaction.atomic
def cancel(reservation, status, user, lang="ar", reason=""):
    """Cancel or mark a no-show, and run the S5 cancellation policy on a held deposit, in one transaction (the steps of
    ``appointments.services.cancel_with_deposit_policy``).

    The reservation row is locked first, then its held deposit is read: ``deposits.services.take`` locks the same row, so a
    deposit taken at the same moment is either seen here or refused there. The status is set before the policy runs (a no-show
    forfeits whatever the hours). Late → forfeit into a «رسوم إلغاء» invoice (its own savepoint: a refusal keeps the cancel);
    early → refund through HG-068's gate.

    Returns {"reservation", "deposit", "outcome", "invoice", "detail"}; ``outcome`` is None (no held deposit), "forfeited",
    "refunded", "refund_pending", or a refusal that leaves the deposit held as the customer's credit: "forfeit_not_allowed",
    "forfeit_refused", "refund_not_allowed", "refund_blocked"."""

    from approvals.gate import hold
    from deposits import services as deposits

    words = MESSAGES[lang]
    if status not in (ReservationStatus.CANCELLED, ReservationStatus.NO_SHOW):
        raise ValidationError(words["bad_status"])
    reservation = _locked(reservation)
    if reservation.status not in OPEN:
        raise ValidationError(words["state"].format(status=status_label(reservation, lang)))
    _target(reservation, None, words)
    deposit = deposits.held_for(reservation)
    before = reservation.status
    reservation.status = status
    reservation.save(update_fields=["status", "updated_at"])
    _audit(reservation, user, "cancel_reservation" if status == ReservationStatus.CANCELLED else "no_show_reservation",
           {"status": status, **({"deposit": deposit.payment.payment_number} if deposit else {})}, {"status": before}, reason=(reason or "").strip()[:255])
    result = {"reservation": reservation, "deposit": deposit, "outcome": None, "invoice": None, "detail": ""}
    if deposit is None:
        return result
    note = (reason or "").strip() or (f"Booking {reservation.number} cancelled" if lang == "en" else f"إلغاء الحجز {reservation.number}")
    if deposits.policy(reservation) == "forfeit":
        if not user_has_permission(user, deposits.SELL):
            result["outcome"] = "forfeit_not_allowed"
            return result
        try:
            with transaction.atomic():
                result["invoice"] = deposits.forfeit(deposit, user, lang, note)
        except ValidationError as exc:
            result.update(outcome="forfeit_refused", detail=" ".join(exc.messages))
        else:
            result["outcome"] = "forfeited"
        return result
    if not user_has_permission(user, deposits.RECEIVE):
        result["outcome"] = "refund_not_allowed"
        return result
    blocker = deposits.refund_blocker(deposit, lang)
    if blocker:
        result.update(outcome="refund_blocked", detail=blocker)
        return result
    if hold(user, "sales.cancel_payment", deposit.payment_id, {"reason": note}, deposit.payment.payment_number) is not None:  # HG-068
        result["outcome"] = "refund_pending"
        return result
    deposits.refund(deposit, user, note, lang)
    result["outcome"] = "refunded"
    return result


def take_deposit(reservation, cashbox, amount, user, lang="ar"):
    """A deposit on the reservation: ``deposits.services.take`` as it is (a payment on account; the walk-in, a switched-off
    feature and a reservation that is not booked or confirmed are refused there). The cashbox must be the reservation's branch's."""

    from deposits import services as deposits

    words = MESSAGES[lang]
    with transaction.atomic():
        # the reservation as it is now, under the lock ``save_reservation`` takes: its customer may have changed since the
        # screen loaded it, and the deposit must be that customer's (Codex)
        reservation = _locked(reservation)
        if reservation.customer_id is None:
            raise ValidationError(words["no_customer"])
        if cashbox is None or not cashboxes(reservation).filter(pk=cashbox.pk).exists():
            raise ValidationError(words["cashbox"])
        return deposits.take(reservation, reservation.customer, cashbox, amount, user, lang)


def deposit_released(reservation):
    """A seated reservation whose order was cancelled, or whose bill was cancelled: its held deposit no longer belongs to a
    live bill, so it may be refunded from the reservation screen (Codex on #422). Forfeiting stays refused: the guests came."""

    if reservation.status != ReservationStatus.SEATED or reservation.order_id is None:
        return False
    order = Order.objects.select_related("invoice").get(pk=reservation.order_id)
    if order.status == OrderStatus.CANCELLED:
        return True
    return order.invoice_id is not None and order.invoice.status == "cancelled"


# --- the deposit at the bill -----------------------------------------------------

def bill_deposit(order, customer):
    """The seated reservation of this order (or of an order merged into it) whose held deposit is ``customer``'s, as
    (reservation, deposit); (None, None) when there is none. A deposit of another customer is not used: it stays held."""

    from deposits import services as deposits

    if order is None or customer is None:
        return None, None
    from django.db.models import Q

    for reservation in TableReservation.objects.filter(Q(order=order) | Q(order__merged_into=order), status=ReservationStatus.SEATED).order_by("pk"):
        deposit = deposits.held_for(reservation)
        if deposit is not None and deposit.payment.customer_id == customer.pk:
            return reservation, deposit
    return None, None


def settle_at_bill(reservation, invoice, expected, lang="ar"):
    """After the bill posted: point the held deposit at the invoice and apply it (S5's ``attach`` and ``apply``, as posting
    would). ``expected`` is what the bill left for the deposit; anything else undoes the whole payment."""

    from deposits import services as deposits
    from deposits.models import DepositStatus
    from settlements.models import InvoiceSettlement, SettlementSource

    deposit = deposits.attach(reservation, invoice)
    applied = deposits.apply(invoice) if deposit is not None else []
    settled = sum((row.amount for row in InvoiceSettlement.objects.filter(invoice=invoice, source=SettlementSource.DEPOSIT)), Decimal("0.00"))
    if deposit is None or not applied or settled != expected:
        raise ValidationError(MESSAGES[lang]["deposit_mismatch"])
    deposit.refresh_from_db()
    if deposit.status != DepositStatus.APPLIED:
        raise ValidationError(MESSAGES[lang]["deposit_mismatch"])
    _audit(reservation, None, "reservation_deposit_at_bill", {"invoice": invoice.invoice_number, "amount": str(settled), "payment": deposit.payment.payment_number})
    return settled


# --- the public page ---------------------------------------------------------------

def page_for(entity, make=True):
    """The branch's public page settings (made on first need, switched off)."""

    found = ReservationPage.objects.filter(entity=entity).first()
    if found is None and make:
        try:
            with transaction.atomic():
                found = ReservationPage.objects.create(entity=entity)
        except IntegrityError:
            found = ReservationPage.objects.get(entity=entity)
    return found


def set_accepting(entity, on, user):
    page = page_for(entity)
    before = page.accepting
    page.accepting = bool(on)
    page.save(update_fields=["accepting", "updated_at"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action="reservation_page", object_type="restaurant.ReservationPage",
                            object_id=str(page.pk), before_data={"accepting": before}, after_data={"accepting": page.accepting, "entity": entity.pk})
    return page


def rotate(entity, user):
    """«غيّر الرابط»: a new key; the old link answers 404 at once."""

    from .models import new_qr_key

    page = page_for(entity)
    page.public_key = new_qr_key()
    page.save(update_fields=["public_key", "updated_at"])
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="restaurant", action="rotate_reservation_page", object_type="restaurant.ReservationPage",
                            object_id=str(page.pk), after_data={"entity": entity.pk})
    return page


def page_by_key(key):
    """The page behind a public key, or None (one answer for every invalid case): unknown or over-long key, the page switched
    off, the tables module off, or the branch inactive."""

    from settings_core.setup_services import module_is_enabled

    from .qr import KEY_MAX, MODULE

    if not key or len(key) > KEY_MAX or not module_is_enabled(MODULE):
        return None
    page = ReservationPage.objects.select_related("entity").filter(public_key=key).first()
    if page is None or not page.accepting or not page.entity.active:
        return None
    return page


def public_url(page, lang="ar"):
    """The page's address, from ``settings.PUBLIC_BASE_URL`` only (as the QR cards); None when it is not set."""

    from django.conf import settings
    from django.urls import reverse

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{reverse('book_table', args=[page.public_key])}?lang={lang}"


@transaction.atomic
def request_online(page, data, lang="ar"):
    """A guest's request from the public page: ``booked``, no table, no customer, ``online``; staff confirm it."""

    words = MESSAGES[lang]
    values = clean(data, words, public=True)
    reservation = _create(values, entity=page.entity, online=True)
    _audit(reservation, None, "request_reservation", {"number": reservation.number, "entity": page.entity_id, "party_size": reservation.party_size,
                                                       "starts_at": reservation.starts_at.isoformat()}, event=AuditEventType.CREATE)
    return reservation
