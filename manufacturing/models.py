from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class WorkCentreKind(models.TextChoices):
    MACHINE = "machine", "Machine"
    LINE = "line", "Production line"
    MANUAL = "manual", "Manual table"


class WorkCentre(models.Model):
    """Manufacturing F6a (HG-133): a place where a stage is done (a machine, a line, a manual table), with the hours it can
    work in a day and one fully loaded rate per hour (labour, power and the machine's wear; owner question 15)."""

    code = models.CharField(max_length=20, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=10, choices=WorkCentreKind.choices, default=WorkCentreKind.MACHINE)
    fixed_asset = models.ForeignKey("fixed_assets.FixedAsset", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    rate_per_hour = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.CheckConstraint(condition=models.Q(hours_per_day__gt=0) & models.Q(hours_per_day__lte=24), name="mfg_workcentre_hours"),
                       models.CheckConstraint(condition=models.Q(rate_per_hour__gte=0), name="mfg_workcentre_rate")]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"

    def name(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar


class Recipe(models.Model):
    """MFG-001: what one batch of a product takes (a bill of materials)."""

    code = models.CharField(max_length=20, unique=True)
    product = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="recipes")
    name = models.CharField(max_length=255, blank=True)
    output_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1, help_text="Units of the product one batch yields.")
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    # Manufacturing F3 (HG-131): days from the run date to the product lot's expiry; NULL = the product has no shelf life.
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recipes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name or self.product.item_name}"


class RecipeStep(models.Model):
    """Manufacturing F6a (HG-133): one step of a recipe's routing. Its orders copy the steps as their stages (a work centre
    and the planned minutes = setup + minutes per batch x batches); a recipe with no step keeps the factory's stage list."""

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    stage_key = models.CharField(max_length=30)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    work_centre = models.ForeignKey(WorkCentre, on_delete=models.PROTECT, related_name="steps")
    minutes_per_batch = models.DecimalField(max_digits=10, decimal_places=2)
    setup_minutes = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [models.UniqueConstraint(fields=["recipe", "order"], name="mfg_one_step_per_position"),
                       models.UniqueConstraint(fields=["recipe", "stage_key"], name="mfg_one_step_per_key"),
                       models.CheckConstraint(condition=models.Q(minutes_per_batch__gt=0) & models.Q(setup_minutes__gte=0), name="mfg_step_minutes")]


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="recipe_uses")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Per batch.")

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["recipe", "component"], name="mfg_one_line_per_component")]


class RunStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    CANCELLED = "cancelled", "Cancelled"


class ProductionRun(models.Model):
    """One production: components out of stock, the product into stock at their cost."""

    number = models.CharField(max_length=30, unique=True)
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="runs")
    batches = models.DecimalField(max_digits=14, decimal_places=3)
    location = models.ForeignKey("master_data.Location", on_delete=models.PROTECT, related_name="production_runs")
    run_date = models.DateField()
    output_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2)  # materials consumed
    # HG-036: labour and overhead absorbed into the product's stock value, and
    # the units that failed a stage (scrap) — made, but never put into stock.
    conversion_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    scrap_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    output_operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, related_name="production_output")
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.POSTED)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_runs")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class ProductionConsumption(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name="consumptions")
    operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, related_name="production_consumption")

    class Meta:
        ordering = ["pk"]


class IssueKind(models.TextChoices):
    """Manufacturing F12 (HG-132): what an order's material row is."""

    ISSUE = "issue", "Issue"
    EXTRA = "extra", "Extra issue"
    RETURN = "return", "Return"


class OrderMaterialIssue(models.Model):
    """HG-036: a component issued to the floor when an order starts. Its value
    sits in work in progress until the order finishes (or is cancelled).

    Manufacturing F12 (HG-132): ``kind`` tells the start's issue (every row made before F12) from an extra issue while
    the order is on the floor (an adjustment out at the average) and a return of what was not used (an adjustment in
    at the cost this order was issued at). ``reason`` is what the person wrote for an extra or a return."""

    order = models.ForeignKey("ProductionOrder", on_delete=models.CASCADE, related_name="issues")
    operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, related_name="order_issue")
    kind = models.CharField(max_length=10, choices=IssueKind.choices, default=IssueKind.ISSUE)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["pk"]


class OrderStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In progress"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class ProductionOrder(models.Model):
    """MFG-002: an order to make a quantity of a product, followed stage by
    stage on the shop floor. Nothing moves in stock until it is finished; the
    finish posts an ordinary production run (MFG-001)."""

    number = models.CharField(max_length=30, unique=True)
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="orders")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    location = models.ForeignKey("master_data.Location", on_delete=models.PROTECT, related_name="production_orders")
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="production_orders")
    due_date = models.DateField(null=True, blank=True)
    # F1 (HG-098): the sales order line this order is made for (make to order); NULL for stock production.
    sales_order_line = models.ForeignKey("orders.SalesOrderLine", on_delete=models.PROTECT, null=True, blank=True, related_name="production_orders")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PLANNED, db_index=True)
    stages = models.JSONField(default=list)  # [[key, name_ar, name_en], ...] fixed when the order is opened
    stage_index = models.PositiveSmallIntegerField(default=0)
    labor_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overhead_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    run = models.OneToOneField(ProductionRun, on_delete=models.PROTECT, null=True, blank=True, related_name="order")
    notes = models.CharField(max_length=255, blank=True)
    # Manufacturing F6b (HG-134): the user's own rank for «رتّب» (lowest first) among orders with the same due date; NULL = no rank.
    schedule_rank = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="production_orders")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.number


class OrderStageLog(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="stage_logs")
    stage_key = models.CharField(max_length=30)
    good_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    defect_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    note = models.CharField(max_length=255, blank=True)
    # Manufacturing F6a (HG-133): how long the stage took, in minutes (typed on the floor; F8 may measure it). NULL = not recorded.
    minutes = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    done_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["done_at", "pk"]


class OrderStep(models.Model):
    """Manufacturing F6b (HG-134): one stage of one order in the production schedule, with the work centre it is planned on,
    its planned minutes and its planned start and end.

    ``planned_start`` and ``planned_end`` are NULL until «رتّب» or a move gives them; they hold a virtual working-day clock (a
    working day starts at 08:00 local time and the hours into it are working minutes), so read the day through
    ``schedule.day_of``, never as wall-clock time. ``finished_at`` is filled when the stage is advanced; ``started_at`` is
    left for the floor screen (F8). A schedule record only: no stock, cost or ledger row is written or read from it."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="steps")
    index = models.PositiveSmallIntegerField()
    stage_key = models.CharField(max_length=30)
    work_centre = models.ForeignKey(WorkCentre, on_delete=models.PROTECT, null=True, blank=True, related_name="order_steps")
    planned_minutes = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    planned_start = models.DateTimeField(null=True, blank=True)
    planned_end = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order_id", "index"]
        constraints = [models.UniqueConstraint(fields=["order", "index"], name="mfg_one_step_per_index"),
                       models.CheckConstraint(condition=models.Q(planned_minutes__isnull=True) | models.Q(planned_minutes__gte=0), name="mfg_orderstep_minutes")]


class LotUse(models.Model):
    """Manufacturing F3 (HG-131): which lot of an ingredient a run (or an order's start) used, and how much.

    Written at the moment of the issue as the batch positions before it minus the positions after it (see
    ``manufacturing.lots``); never recomputed later, because positions move with stock. ``batch`` NULL is the quantity no
    lot covered ("no lot"). A tracking record only: no stock, cost or ledger row is written or read here."""

    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, null=True, blank=True, related_name="lot_uses")
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, null=True, blank=True, related_name="lot_uses")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("batches.Batch", on_delete=models.PROTECT, null=True, blank=True, related_name="uses")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(run__isnull=False) | models.Q(order__isnull=False), name="mfg_lotuse_has_run_or_order"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="mfg_lotuse_positive"),
        ]


class OrderStandardLine(models.Model):
    """Manufacturing F12 (HG-132): the standard of one component for one order, written when the order starts (the
    recipe's quantity times the order's batches, at the cost it was issued at). The variance report reads it, so an
    edit of the recipe later never rewrites what an order was measured against."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="standard_lines")
    component = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["order", "component"], name="mfg_one_standard_per_component")]


class CostLineKind(models.TextChoices):
    LABOUR = "labour", "Labour"
    OVERHEAD = "overhead", "Overhead"
    OUTSIDE = "outside", "Outside processing"


class OrderCostLine(models.Model):
    """Manufacturing F12 (HG-132): labour or overhead actually spent on an order (hours x rate, or an amount), added
    and cancelled until the finish. ``outside`` is reserved for processing at an outside workshop (F4).

    At the finish, each kind's live lines replace that kind's planned figure on the order (``labor_cost``,
    ``overhead_cost``); a kind with no live line keeps its planned figure."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="cost_lines")
    kind = models.CharField(max_length=10, choices=CostLineKind.choices)
    description = models.CharField(max_length=255, blank=True)
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # Manufacturing F6a (HG-133): a line confirmed from a work centre's suggestion carries the centre and the stage log whose
    # minutes it priced (so one stage is confirmed once); both NULL on every other line.
    work_centre = models.ForeignKey("WorkCentre", on_delete=models.PROTECT, null=True, blank=True, related_name="cost_lines")
    stage_log = models.ForeignKey("OrderStageLog", on_delete=models.PROTECT, null=True, blank=True, related_name="cost_lines")
    hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="mfg_costline_positive")]
