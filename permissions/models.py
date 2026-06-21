from django.db import models


class PermissionModule(models.TextChoices):
    SETTINGS = "settings", "Settings"
    MASTER_DATA = "master_data", "Master data"
    INVENTORY = "inventory", "Inventory"
    PURCHASES = "purchases", "Purchases"
    SALES = "sales", "Sales"
    CASHBOXES = "cashboxes", "Cashboxes"
    REPORTS = "reports", "Reports"
    CLOSING = "closing", "Closing"
    AUDIT = "audit", "Audit"
    IMPORTS = "imports", "Imports"
    BARCODE = "barcode", "Barcode"


class RoleCode(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    CASHIER = "cashier", "Cashier"
    STOCK_KEEPER = "stock_keeper", "Stock Keeper"
    ACCOUNTANT = "accountant", "Accountant"
    SUPPORT = "support", "Support"


class Permission(models.Model):
    """Atomic capability used by Hesba permission checks.

    Reports are read-only permissions. Sensitive finance permissions must be
    granted deliberately and never implied from sales access.
    """

    code = models.CharField(max_length=120, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    module = models.CharField(max_length=30, choices=PermissionModule.choices)
    description = models.TextField(blank=True)
    is_report_permission = models.BooleanField(default=False)
    is_sensitive_finance = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["module", "code"]
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return self.code


class Role(models.Model):
    code = models.CharField(max_length=40, choices=RoleCode.choices, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    is_system_role = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(
        Permission,
        through="RolePermission",
        related_name="roles",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Role"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.name_ar or self.code


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    allow = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="unique_role_permission"),
        ]
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self):
        return f"{self.role.code}: {self.permission.code}"
