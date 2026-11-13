from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from config.money import money_round


class DiningTable(models.Model):
    """RESTO-001: a table in the hall (or a seat at the bar)."""

    name = models.CharField(max_length=40, unique=True)
    area = models.CharField(max_length=60, blank=True, help_text="Hall, terrace, family section…")
    seats = models.PositiveSmallIntegerField(default=4)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class OrderKind(models.TextChoices):
    DINE_IN = "dine_in", "Dine in"
    TAKEAWAY = "takeaway", "Takeaway"
    DELIVERY = "delivery", "Delivery"


class OrderStatus(models.TextChoices):
    OPEN = "open", "Open"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class Order(models.Model):
    """An order being taken; paying it makes one posted sales invoice through the POS engine."""

    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=OrderKind.choices, default=OrderKind.DINE_IN)
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.OPEN)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="restaurant_orders")
    waiter = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="restaurant_orders")
    guests = models.PositiveSmallIntegerField(default=1)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    #: RESTO-003: who takes a delivery out, when it left, and what the customer pays for it (billed as its own invoice line).
    driver = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivery_fee = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="restaurant_order")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="restaurant_orders")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            # One open order per table: the second waiter joins the first's order.
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="restaurant_one_open_order_per_table"),
        ]

    def __str__(self):
        return self.number

    def live_lines(self):
        return [line for line in self.lines.all() if not line.voided]

    @property
    def total(self):
        return money_round(sum((line.amount for line in self.live_lines()), Decimal("0.00")) + (self.delivery_fee or Decimal("0")))


class KitchenStation(models.Model):
    """Restaurants R4 (HG-097): a place that cooks or pours part of the menu (the kitchen, the bar, the sweets counter).

    Sending an order to the kitchen makes one ticket per station; an item goes to the station that holds its category, and
    whatever no station holds goes to the default one. It belongs to one entity (a record made without one belongs to the main
    entity, as a cashbox does), so a branch's screen only ever shows that branch's stations."""

    name_ar = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60, blank=True)
    sort = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Takes every item whose category no station holds.")
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="kitchen_stations")
    categories = models.ManyToManyField("master_data.Category", blank=True, related_name="kitchen_stations")

    class Meta:
        ordering = ["sort", "pk"]

    def __str__(self):
        return self.name_ar

    def label(self, lang="ar"):
        return (self.name_en if lang == "en" and self.name_en else self.name_ar)

    def save(self, *args, **kwargs):
        if self.entity_id is None:  # as Cashbox.save: a record made without an entity belongs to the main one
            from entities.services import main_entity

            self.entity = main_entity()
        super().save(*args, **kwargs)


class KitchenTicket(models.Model):
    """What was sent to a kitchen station in one go; printed on that station's printer and shown on its screen.

    ``station`` is empty for a ticket made with no station set up (and for every ticket made before stations existed): it then
    holds the whole send, exactly as before, and belongs to the main entity."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tickets")
    sequence = models.PositiveSmallIntegerField()
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="kitchen_tickets")
    sent_at = models.DateTimeField(auto_now_add=True)
    station = models.ForeignKey(KitchenStation, on_delete=models.PROTECT, null=True, blank=True, related_name="tickets")
    done_at = models.DateTimeField(null=True, blank=True, help_text="When the station finished the whole ticket (tap the card).")

    class Meta:
        ordering = ["order", "sequence"]
        constraints = [models.UniqueConstraint(fields=["order", "sequence"], name="restaurant_ticket_sequence")]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="restaurant_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=120, blank=True, help_text="For the kitchen: no onions, extra spicy…")
    ticket = models.ForeignKey(KitchenTicket, on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")
    voided = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    #: R4: when the station marked this line ready. On a voided line the same stamp means "the kitchen has seen the void":
    #: voiding clears it, and the kitchen's tap on the struck-through line sets it again.
    ready_at = models.DateTimeField(null=True, blank=True)
    #: R4 (from R13's audit-only reason): why a line that had gone to the kitchen was voided, shown on the order and on the card.
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["pk"]

    @property
    def amount(self):
        return money_round(self.quantity * self.unit_price)


class DishIngredient(models.Model):
    """RESTO-002: what one plate of a dish uses, for its cost and margin (HG-044, read-only part).

    Reading only: nothing here takes ingredients out of stock. Selling a dish
    deducts nothing until Ahmed decides HG-044's ingredient deduction."""

    dish = models.ForeignKey("master_data.Item", on_delete=models.CASCADE, related_name="dish_ingredients")
    ingredient = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="used_in_dishes")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, help_text="Per plate, in the ingredient's unit.")

    class Meta:
        ordering = ["dish", "ingredient__item_name"]
        constraints = [models.UniqueConstraint(fields=["dish", "ingredient"], name="restaurant_one_line_per_ingredient")]

    def __str__(self):
        return f"{self.dish_id}:{self.ingredient_id}"
