from django.db import models


class Location(models.Model):
    """Physical or logical place where inventory can exist."""

    location_code = models.CharField(max_length=50, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_receiving_location = models.BooleanField(default=True)
    is_selling_location = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location_code"]
        verbose_name = "Location"
        verbose_name_plural = "Locations"

    def __str__(self):
        return f"{self.location_code} - {self.name_ar}"


class Category(models.Model):
    category_code = models.CharField(max_length=50, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category_code"]
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return f"{self.category_code} - {self.name_ar}"


class Item(models.Model):
    """Stock item, service item, or telecom balance unit.

    Search label rule: Item_Code - Item_Name - Size - Color.
    Cost fields exist for controlled logic but must be hidden from users without
    cost/profit permissions.
    """

    item_code = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(max_length=120, blank=True, db_index=True)
    item_name = models.CharField(max_length=255)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="items",
        null=True,
        blank=True,
    )
    size = models.CharField(max_length=80, blank=True)
    color = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=50, default="unit")
    default_sale_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    default_purchase_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    average_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    min_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    is_stock_tracked = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    import_batch_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item_code"]
        indexes = [
            models.Index(fields=["item_code"]),
            models.Index(fields=["item_name"]),
            models.Index(fields=["barcode"]),
        ]
        verbose_name = "Item"
        verbose_name_plural = "Items"

    @property
    def search_label(self):
        parts = [self.item_code, self.item_name, self.size, self.color]
        return " - ".join(part for part in parts if part)

    def __str__(self):
        return self.search_label


class Customer(models.Model):
    customer_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    import_batch_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["customer_code"]
        indexes = [
            models.Index(fields=["customer_code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["phone"]),
        ]
        verbose_name = "Customer"
        verbose_name_plural = "Customers"

    def __str__(self):
        return f"{self.customer_code} - {self.name}"


class Supplier(models.Model):
    supplier_code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    import_batch_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["supplier_code"]
        indexes = [
            models.Index(fields=["supplier_code"]),
            models.Index(fields=["name"]),
            models.Index(fields=["phone"]),
        ]
        verbose_name = "Supplier"
        verbose_name_plural = "Suppliers"

    def __str__(self):
        return f"{self.supplier_code} - {self.name}"
