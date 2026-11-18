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
    # Manufacturing F5 (HG-136): an order of this recipe finishes into the entity's quality hold place (``QC-<entity>``)
    # instead of its own location; the goods are released or rejected from the quality screen.
    needs_release = models.BooleanField(default=False)
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
    and cancelled until the finish. ``outside`` is the fee of a stage done at an outside supplier (Manufacturing F4, HG-137):
    written by the order's finish from the supplier's posted bill (``outside.absorb``), never typed.

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


class CriterionKind(models.TextChoices):
    PASS_FAIL = "pass_fail", "Pass or fail"
    NUMBER = "number", "A number within limits"


class QualityCriterion(models.Model):
    """Manufacturing F5 (HG-136): one thing a quality check looks at: for a recipe's product (the in-process quality stage
    and the check before release) or for a bought item (an incoming check of a purchase received into the hold place).

    ``pass_fail`` is answered yes or no; ``number`` is a measured value that must sit within ``min_value`` and
    ``max_value`` (either may be empty). A check keeps its own copy of the name and limits (``QualityResult``), so
    editing the list never rewrites a check already recorded."""

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, null=True, blank=True, related_name="criteria")
    item = models.ForeignKey("master_data.Item", on_delete=models.CASCADE, null=True, blank=True, related_name="quality_criteria")
    order = models.PositiveSmallIntegerField()
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=10, choices=CriterionKind.choices, default=CriterionKind.PASS_FAIL)
    min_value = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        ordering = ["order", "pk"]
        constraints = [
            models.CheckConstraint(condition=(models.Q(recipe__isnull=False) & models.Q(item__isnull=True))
                                   | (models.Q(recipe__isnull=True) & models.Q(item__isnull=False)), name="mfg_criterion_recipe_or_item"),
            models.CheckConstraint(condition=models.Q(min_value__isnull=True) | models.Q(max_value__isnull=True)
                                   | models.Q(min_value__lte=models.F("max_value")), name="mfg_criterion_limits"),
        ]

    def name(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar


class CheckKind(models.TextChoices):
    FINAL = "final", "Finished goods on hold"
    STAGE = "stage", "In-process quality stage"
    INCOMING = "incoming", "Incoming purchase"


class CheckResult(models.TextChoices):
    PASSED = "passed", "Passed"
    HELD = "held", "Kept on hold"
    REJECTED = "rejected", "Rejected"
    PARTLY = "partly", "Partly passed"


class QualityCheck(models.Model):
    """Manufacturing F5 (HG-136): a dated quality check with its decision.

    * ``final``: the goods of a finished order waiting in the hold place. The passed quantity is released to the order's
      location (``release_operation``, an ordinary transfer); the rejected quantity is written off from the hold place
      (``reject_operation``, an ordinary adjustment out). A check that decides nothing keeps the goods on hold.
    * ``incoming``: a purchase line received into the hold place. The passed quantity is released to ``destination``;
      the rejected quantity stays in the hold place to go back on an ordinary purchase return (no operation).
    * ``stage``: the criteria recorded with an order's quality stage (``stage_log``). Nothing moves: failed units are
      the stage's defects (scrap), as before.

    ``quantity`` is what was waiting (or entered the stage) when the check was recorded. What still waits is never
    stored: it is the made quantity less the passed and rejected quantities whose operations are still posted."""

    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=10, choices=CheckKind.choices)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="quality_checks")
    purchase_line = models.ForeignKey("purchases.PurchaseLine", on_delete=models.PROTECT, null=True, blank=True, related_name="quality_checks")
    stage_log = models.OneToOneField(OrderStageLog, on_delete=models.CASCADE, null=True, blank=True, related_name="quality_check")
    checked_on = models.DateField()
    result = models.CharField(max_length=10, choices=CheckResult.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    passed_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    note = models.CharField(max_length=255, blank=True)
    destination = models.ForeignKey("master_data.Location", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    release_operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="quality_release")
    reject_operation = models.OneToOneField("inventory.StockOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="quality_reject")
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=(models.Q(order__isnull=False) & models.Q(purchase_line__isnull=True))
                                   | (models.Q(order__isnull=True) & models.Q(purchase_line__isnull=False)), name="mfg_check_order_or_purchase"),
            models.CheckConstraint(condition=models.Q(quantity__gte=0) & models.Q(passed_quantity__gte=0) & models.Q(rejected_quantity__gte=0),
                                   name="mfg_check_quantities"),
            models.CheckConstraint(condition=models.Q(quantity__gte=models.F("passed_quantity") + models.F("rejected_quantity")),
                                   name="mfg_check_within_quantity"),
        ]

    def __str__(self):
        return self.number


class QualityResult(models.Model):
    """Manufacturing F5 (HG-136): what one criterion read on one check, with the criterion's name and limits as they
    were that day (``criterion`` is only a pointer and may be gone)."""

    quality_check = models.ForeignKey(QualityCheck, on_delete=models.CASCADE, related_name="results")
    criterion = models.ForeignKey(QualityCriterion, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=10, choices=CriterionKind.choices)
    min_value = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    ok = models.BooleanField()

    class Meta:
        ordering = ["pk"]

    def name(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar


class OutsideStatus(models.TextChoices):
    SENT = "sent", "At the supplier"
    BACK = "back", "Back"
    BILLED = "billed", "Billed"
    CANCELLED = "cancelled", "Cancelled"


class OutsideJob(models.Model):
    """Manufacturing F4 (HG-137): one stage of an order done at an outside supplier («تشغيل لدى الغير»).

    A record of where the order's units are, never a stock document: the units were issued to work in progress when
    the order started, so sending them out and taking them back writes no stock movement and no journal row. The
    stage is closed (good and defective, like any stage) when the units come back. ``purchase_invoice`` is the
    supplier's bill: an ordinary purchase invoice made as a draft of one service line (``returned_good`` x
    ``unit_price``) and posted from the purchases screen. ``cost_line`` is the ``OrderCostLine(kind=outside)`` the
    order's finish wrote from that posted bill, net of its purchase returns: the fee that joined the product's cost.

    ``status`` is what this module wrote. A bill cancelled from the purchases screen is read, never stored: such a
    job counts as back and takes a new bill (``outside.state``)."""

    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="outside_jobs")
    stage_key = models.CharField(max_length=30)
    supplier = models.ForeignKey("master_data.Supplier", on_delete=models.PROTECT, related_name="outside_jobs")
    quantity_sent = models.DecimalField(max_digits=14, decimal_places=3)
    # Agreed per good unit that comes back, before tax. 0 = not agreed yet (set later by someone who may see cost, before the bill).
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sent_on = models.DateField()
    returned_good = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    returned_defective = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    returned_on = models.DateField(null=True, blank=True)
    purchase_invoice = models.OneToOneField("purchases.PurchaseInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="outside_job")
    cost_line = models.OneToOneField(OrderCostLine, on_delete=models.PROTECT, null=True, blank=True, related_name="outside_job")
    status = models.CharField(max_length=10, choices=OutsideStatus.choices, default=OutsideStatus.SENT)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity_sent__gt=0) & models.Q(unit_price__gte=0), name="mfg_outside_sent_and_price"),
            models.CheckConstraint(condition=models.Q(returned_good__gte=0) & models.Q(returned_defective__gte=0), name="mfg_outside_returned"),
            models.CheckConstraint(condition=models.Q(quantity_sent__gte=models.F("returned_good") + models.F("returned_defective")),
                                   name="mfg_outside_within_sent"),
            # A stage is out once at a time; a cancelled sending leaves the stage free to be sent again.
            models.UniqueConstraint(fields=["order", "stage_key"], condition=~models.Q(status="cancelled"), name="mfg_one_live_outside_job_per_stage"),
        ]

    def __str__(self):
        return f"{self.order_id}:{self.stage_key}"
