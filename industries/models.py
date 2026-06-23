from django.db import models


class SectorCode(models.TextChoices):
    STORE = "store", "Store"
    SERVICES = "services", "Services"
    CONSTRUCTION = "construction", "Construction"
    FACTORY = "factory", "Factory"


class SectorModule(models.Model):
    code = models.CharField(max_length=40, choices=SectorCode.choices, unique=True)
    name_ar = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"
