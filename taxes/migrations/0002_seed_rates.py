from decimal import Decimal

from django.db import migrations


RATES = (
    # code, name_ar, name_en, rate, eta_type, eta_subtype, is_default
    ("VAT14", "ضريبة القيمة المضافة", "VAT", Decimal("14.00"), "T1", "V009", True),
    ("EXEMPT", "معفى من الضريبة", "Exempt", Decimal("0.00"), "T1", "V003", False),
)


def seed(apps, schema_editor):
    TaxRate = apps.get_model("taxes", "TaxRate")
    for code, name_ar, name_en, rate, eta_type, eta_subtype, is_default in RATES:
        TaxRate.objects.get_or_create(
            code=code,
            defaults={"name_ar": name_ar, "name_en": name_en, "rate": rate, "eta_type": eta_type, "eta_subtype": eta_subtype, "is_default": is_default},
        )


class Migration(migrations.Migration):
    dependencies = [("taxes", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
