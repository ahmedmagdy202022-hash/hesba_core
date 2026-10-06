from django.conf import settings
from django.db import models


class Recipe(models.Model):
    """MFG-001: what one batch of a product takes (a bill of materials)."""

    code = models.CharField(max_length=20, unique=True)
    product = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="recipes")
    name = models.CharField(max_length=255, blank=True)
    output_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1, help_text="Units of the product one batch yields.")
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recipes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name or self.product.item_name}"


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
    total_cost = models.DecimalField(max_digits=14, decimal_places=2)
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
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PLANNED, db_index=True)
    stages = models.JSONField(default=list)  # [[key, name_ar, name_en], ...] fixed when the order is opened
    stage_index = models.PositiveSmallIntegerField(default=0)
    labor_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overhead_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    run = models.OneToOneField(ProductionRun, on_delete=models.PROTECT, null=True, blank=True, related_name="order")
    notes = models.CharField(max_length=255, blank=True)
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
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    done_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["done_at", "pk"]
