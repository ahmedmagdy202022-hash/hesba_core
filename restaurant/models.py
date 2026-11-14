from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from config.money import money_round


def _main_entity():
    from entities.services import main_entity

    return main_entity()


class DiningTable(models.Model):
    """RESTO-001: a table in the hall (or a seat at the bar).

    Restaurants R12 (HG-108): a table belongs to one entity (branch), and its name is unique within that entity, so every
    branch can have its «T1». The column is NOT NULL, unlike the older entity columns where an empty entity means the main
    one (HG-034): a unique constraint treats NULLs as different, so with NULL rows two main-entity «T1» would pass the
    database. A table made without an entity gets the main one in ``save()``, as a location does."""

    name = models.CharField(max_length=40)
    area = models.CharField(max_length=60, blank=True, help_text="Hall, terrace, family section…")
    seats = models.PositiveSmallIntegerField(default=4)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, related_name="dining_tables")

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["entity", "name"], name="restaurant_table_name_per_entity")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.entity_id is None:
            self.entity = _main_entity()
        super().save(*args, **kwargs)


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
    #: R12 (HG-108): the branch the order belongs to, set when it is opened: the table's for dine-in, the working entity's for
    #: takeaway and delivery. Its bill posts to this entity's selling store and cashbox, whatever entity the biller works in.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, related_name="restaurant_orders")

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            # One open order per table: the second waiter joins the first's order.
            models.UniqueConstraint(fields=["table"], condition=Q(status="open"), name="restaurant_one_open_order_per_table"),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if self.entity_id is None:  # the services always say; a caller written before R12 gets the table's, else the main one
            self.entity_id = self.table.entity_id if self.table_id else _main_entity().pk
        super().save(*args, **kwargs)

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
            self.entity = _main_entity()
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
    #: R1a (HG-109): a priced add-on is its own line under its dish (``restaurant.modifiers``): the dish's quantity, its own
    #: item's price. It moves with its dish and never on its own. Empty on every dish and on every line made before R1a.
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    #: R1a: what was chosen, as printed: on a dish, its free choices («ميديم · من غير بصل»); on an add-on, its option's name
    #: («جبنة زيادة»), which the bill row and the kitchen ticket print. Empty on a plain line.
    options = models.CharField(max_length=255, blank=True, default="")

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


class ModifierGroup(models.Model):
    """Restaurants R1a (HG-109): a question a dish asks when it is ordered: «درجة الاستواء» (choose 1, required), «إضافات»
    (up to 3, optional). The rules live in ``restaurant.modifiers``.

    Company-wide, like the menu itself: the dishes (``master_data.Item``), their recipes (``DishIngredient``) and their sizes
    (``variants.VariantGroup``) carry no entity, so a dish asks the same questions in every branch."""

    name_ar = models.CharField(max_length=80)
    name_en = models.CharField(max_length=80, blank=True)
    min_choices = models.PositiveSmallIntegerField(default=0, help_text="0 means the group is optional.")
    max_choices = models.PositiveSmallIntegerField(default=1)
    sort = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(max_choices__gte=1) & Q(min_choices__lte=models.F("max_choices")), name="restaurant_modifier_group_min_max"),
        ]

    def __str__(self):
        return self.name_ar

    def label(self, lang="ar"):
        return self.name_en if lang == "en" and self.name_en else self.name_ar


class ModifierOption(models.Model):
    """One answer in a group. With an ``item`` it is a priced add-on: its price, VAT rate, e-receipt code and stock are that
    item's, and it becomes its own order line under the dish. Without one it is a free choice: it costs nothing and is
    printed with the dish."""

    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="options")
    name_ar = models.CharField(max_length=80)
    name_en = models.CharField(max_length=80, blank=True)
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="modifier_options")
    sort = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "sort", "pk"]

    def __str__(self):
        return self.name_ar

    def label(self, lang="ar"):
        return self.name_en if lang == "en" and self.name_en else self.name_ar


class MenuItemModifierGroup(models.Model):
    """Which dishes ask which questions, in which order."""

    item = models.ForeignKey("master_data.Item", on_delete=models.CASCADE, related_name="modifier_links")
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="dish_links")
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["item", "sort", "pk"]
        constraints = [models.UniqueConstraint(fields=["item", "group"], name="restaurant_one_link_per_dish_group")]
