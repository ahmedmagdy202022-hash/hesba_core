"""Fixture helpers shared by the service-layer tests.

The master-data helpers reuse an existing row when called with the same code,
so factories that build their own defaults compose without tripping the code
uniqueness constraints.

Invoice models validate their own arithmetic in clean(), so these helpers
build internally consistent rows (remaining_due == total - paid_now, line
totals == quantity x price - discount) and every factory stays callable with
no arguments.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model

from accounts.models import UserProfile
from cashboxes.models import Cashbox, CashboxDirection, CashboxMovement, CashboxMovementType
from inventory.models import StockMovement, StockMovementType
from inventory.services import recalculate_item_average_cost
from master_data.models import Customer, Item, Location, Supplier
from permissions.models import Permission, Role, RoleCode, RolePermission
from purchases.models import PurchaseInvoice, PurchaseInvoiceStatus, PurchaseLine
from sales.models import SalesInvoice, SalesInvoiceStatus, SalesLine, SalesPaymentStatus, money_round


DEFAULT_DATE = date(2026, 1, 15)


def make_user(username="service_tester", **kwargs):
    return get_user_model().objects.create_user(
        username=username,
        password="service-tests-only",
        **kwargs,
    )


def make_user_profile(user=None, role=None, **kwargs):
    """Link a user to a Hesba role.

    ``permissions.user_has_permission`` reads a user's rights through this row,
    so a user without one holds no permissions at all no matter which role
    exists. Tests that exercise gated views need the link, not just the user.
    """

    defaults = {
        "user": user if user is not None else make_user(),
        "role": role,
        "active": True,
    }
    defaults.update(kwargs)
    return UserProfile.objects.create(**defaults)


def make_seeded_role(code=RoleCode.OWNER):
    """Fetch one of the roles the permission seed migration created.

    Prefer this over ``make_role`` when the test cares about real permissions:
    the seeded roles carry the permission matrix, while ``make_role`` builds an
    empty role with a code outside ``RoleCode``.
    """

    return Role.objects.get(code=code)


def make_location(location_code="MAIN", **kwargs):
    defaults = {"name_ar": "المخزن الرئيسي", "name_en": "Main store"}
    defaults.update(kwargs)
    location, _ = Location.objects.get_or_create(
        location_code=location_code, defaults=defaults
    )
    return location


def make_item(item_code="ITEM-001", **kwargs):
    defaults = {
        "item_name": "Test item",
        "is_stock_tracked": True,
        "average_cost": Decimal("0.00"),
    }
    defaults.update(kwargs)
    item, _ = Item.objects.get_or_create(item_code=item_code, defaults=defaults)
    return item


def make_customer(customer_code="CUST-001", **kwargs):
    defaults = {"name": "Test customer"}
    defaults.update(kwargs)
    customer, _ = Customer.objects.get_or_create(
        customer_code=customer_code, defaults=defaults
    )
    return customer


def make_supplier(supplier_code="SUP-001", **kwargs):
    defaults = {"name": "Test supplier"}
    defaults.update(kwargs)
    supplier, _ = Supplier.objects.get_or_create(
        supplier_code=supplier_code, defaults=defaults
    )
    return supplier


def make_cashbox(cashbox_code="CASH-001", **kwargs):
    defaults = {"name_ar": "الخزنة الرئيسية", "name_en": "Main cashbox"}
    defaults.update(kwargs)
    cashbox, _ = Cashbox.objects.get_or_create(
        cashbox_code=cashbox_code, defaults=defaults
    )
    return cashbox


def make_stock_movement(item, location, movement_type, quantity, unit_cost="0.00", **kwargs):
    defaults = {"movement_date": DEFAULT_DATE}
    defaults.update(kwargs)
    return StockMovement.objects.create(
        item=item,
        location=location,
        movement_type=movement_type,
        quantity=Decimal(str(quantity)),
        unit_cost=Decimal(str(unit_cost)),
        **defaults,
    )


def stock_in(item, location, quantity, unit_cost="0.00", **kwargs):
    """Receive stock, the way a posted purchase would."""
    return make_stock_movement(
        item, location, StockMovementType.PURCHASE_IN, quantity, unit_cost, **kwargs
    )


def make_cashbox_movement(cashbox, direction, amount, **kwargs):
    defaults = {
        "movement_date": DEFAULT_DATE,
        "movement_type": (
            CashboxMovementType.DIRECT_IN
            if direction == CashboxDirection.IN
            else CashboxMovementType.DIRECT_OUT
        ),
    }
    defaults.update(kwargs)
    return CashboxMovement.objects.create(
        cashbox=cashbox,
        direction=direction,
        amount=Decimal(str(amount)),
        **defaults,
    )


def make_role(code="ROLE-TEST", **kwargs):
    defaults = {"name_ar": "دور تجريبي", "name_en": "Test role"}
    defaults.update(kwargs)
    return Role.objects.create(code=code, **defaults)


def make_permission(code="perm.test", **kwargs):
    defaults = {"name_ar": "صلاحية تجريبية", "module": "sales"}
    defaults.update(kwargs)
    return Permission.objects.create(code=code, **defaults)


def grant(role, permission, allow=True):
    return RolePermission.objects.create(role=role, permission=permission, allow=allow)


def make_draft_sales_invoice(
    customer=None,
    location=None,
    cashbox=None,
    invoice_number="SI-001",
    paid_now="0.00",
    **kwargs,
):
    """A draft invoice with no lines. Totals stay zero until add_sales_line."""
    defaults = {"invoice_date": DEFAULT_DATE}
    defaults.update(kwargs)
    paid_now = Decimal(str(paid_now))
    return SalesInvoice.objects.create(
        invoice_number=invoice_number,
        customer=customer or make_customer(),
        selling_location=location or make_location(),
        cashbox=cashbox,
        status=SalesInvoiceStatus.DRAFT,
        paid_now=paid_now,
        **defaults,
    )


def add_sales_line(invoice, item, quantity, unit_sale_price, line_number=None, discount="0.00"):
    """Add a line and refresh the invoice totals to stay clean()-valid."""
    quantity = Decimal(str(quantity))
    unit_sale_price = Decimal(str(unit_sale_price))
    discount = Decimal(str(discount))
    line_total = money_round((quantity * unit_sale_price) - discount)

    line = SalesLine.objects.create(
        invoice=invoice,
        line_number=line_number or invoice.lines.count() + 1,
        item=item,
        quantity=quantity,
        unit_sale_price=unit_sale_price,
        line_discount_amount=discount,
        line_total_amount=line_total,
    )

    recalculate_invoice_totals(invoice)
    return line


def recalculate_invoice_totals(invoice, paid_now=None):
    subtotal = sum(
        (line.line_total_amount for line in invoice.lines.all()), Decimal("0.00")
    )
    invoice.subtotal = money_round(subtotal)
    invoice.total_amount = money_round(subtotal)
    if paid_now is not None:
        invoice.paid_now = Decimal(str(paid_now))
    invoice.remaining_due = money_round(invoice.total_amount - invoice.paid_now)
    invoice.payment_status = invoice.calculate_payment_status()
    invoice.save()
    return invoice


def posted_invoice_ready(paid_now="0.00", stock_quantity=10, unit_cost="5.00"):
    """Draft invoice with one line and enough stock on hand to post."""
    location = make_location()
    item = make_item()
    cashbox = make_cashbox()
    stock_in(item, location, stock_quantity, unit_cost)
    # A posted purchase would refresh the stored average cost; do the same so
    # the invoice is costed from real stock instead of a stale zero.
    recalculate_item_average_cost(item)
    item.refresh_from_db()

    invoice = make_draft_sales_invoice(location=location, cashbox=cashbox)
    add_sales_line(invoice, item, quantity=2, unit_sale_price="30.00")
    recalculate_invoice_totals(invoice, paid_now=paid_now)
    invoice.refresh_from_db()
    return invoice, item, location, cashbox


def make_draft_purchase_invoice(
    supplier=None,
    location=None,
    cashbox=None,
    invoice_number="PI-001",
    paid_now="0.00",
    **kwargs,
):
    defaults = {"invoice_date": DEFAULT_DATE}
    defaults.update(kwargs)
    return PurchaseInvoice.objects.create(
        invoice_number=invoice_number,
        supplier=supplier or make_supplier(),
        receiving_location=location or make_location(),
        cashbox=cashbox,
        status=PurchaseInvoiceStatus.DRAFT,
        paid_now=Decimal(str(paid_now)),
        **defaults,
    )


def add_purchase_line(
    invoice, item, quantity, unit_purchase_price, line_number=None, discount="0.00"
):
    quantity = Decimal(str(quantity))
    unit_purchase_price = Decimal(str(unit_purchase_price))
    discount = Decimal(str(discount))
    line_total = money_round((quantity * unit_purchase_price) - discount)

    line = PurchaseLine.objects.create(
        invoice=invoice,
        line_number=line_number or invoice.lines.count() + 1,
        item=item,
        quantity=quantity,
        unit_purchase_price=unit_purchase_price,
        line_discount_amount=discount,
        line_total_amount=line_total,
    )

    recalculate_purchase_totals(invoice)
    return line


def recalculate_purchase_totals(invoice, paid_now=None):
    subtotal = sum(
        (line.line_total_amount for line in invoice.lines.all()), Decimal("0.00")
    )
    invoice.subtotal = money_round(subtotal)
    invoice.total_amount = money_round(subtotal)
    if paid_now is not None:
        invoice.paid_now = Decimal(str(paid_now))
    invoice.remaining_due = money_round(invoice.total_amount - invoice.paid_now)
    invoice.payment_status = invoice.calculate_payment_status()
    invoice.save()
    return invoice


def purchase_ready(paid_now="0.00", quantity=4, unit_purchase_price="25.00"):
    """Draft purchase invoice with one line, ready to post."""
    location = make_location()
    item = make_item()
    cashbox = make_cashbox()

    invoice = make_draft_purchase_invoice(location=location, cashbox=cashbox)
    add_purchase_line(invoice, item, quantity=quantity, unit_purchase_price=unit_purchase_price)
    recalculate_purchase_totals(invoice, paid_now=paid_now)
    invoice.refresh_from_db()
    return invoice, item, location, cashbox
