"""T12 (HG-060): the default salaries account (5201) becomes the one a payroll run's cost posts to.

As with bank charges (0003): ``ensure_chart`` never changes an existing account, so only the default account,
still under its default name, takes the key; a renamed one is left alone and the chart adds its own beside it.
The three payroll liabilities (2107-2109) are new codes, so ``ensure_chart`` adds them by itself.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    if Account.objects.filter(control="salaries").exists():
        return
    Account.objects.filter(code="5201", control="", name_en="Salaries & wages").update(control="salaries", is_system=True)


def backwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Account.objects.filter(code="5201", control="salaries").update(control="", is_system=False)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0004_notes_controls")]

    operations = [migrations.RunPython(forwards, backwards)]
