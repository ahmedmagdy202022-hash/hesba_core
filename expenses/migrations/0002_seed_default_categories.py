from django.db import migrations


CATEGORIES = (
    ("rent", "إيجار", "Rent"),
    ("salaries", "مرتبات وأجور", "Salaries & wages"),
    ("utilities", "كهرباء ومياه وغاز", "Utilities"),
    ("internet_phone", "إنترنت وتليفون", "Internet & phone"),
    ("transport", "نقل ومواصلات", "Transport"),
    ("maintenance", "صيانة وإصلاحات", "Maintenance & repairs"),
    ("marketing", "دعاية وتسويق", "Marketing"),
    ("supplies", "أدوات ومستلزمات", "Supplies"),
    ("fees_taxes", "رسوم وضرائب", "Fees & taxes"),
    ("other", "مصروفات أخرى", "Other expenses"),
)


def seed(apps, schema_editor):
    ExpenseCategory = apps.get_model("expenses", "ExpenseCategory")
    for code, name_ar, name_en in CATEGORIES:
        ExpenseCategory.objects.get_or_create(code=code, defaults={"name_ar": name_ar, "name_en": name_en})


def unseed(apps, schema_editor):
    ExpenseCategory = apps.get_model("expenses", "ExpenseCategory")
    ExpenseCategory.objects.filter(code__in=[row[0] for row in CATEGORIES], expenses__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("expenses", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
