"""T3 (HG-055): the default bank-charges account (5212) becomes the one bank fees post to.

``ensure_chart`` never changes an existing account, so without this an
upgraded install would get a second "Bank charges" account beside 5212. Only
the default account, still under its default name, takes the key.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    if Account.objects.filter(control="bank_charges").exists():
        return
    Account.objects.filter(code="5212", control="", name_en="Bank charges").update(control="bank_charges", is_system=True)


def backwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Account.objects.filter(code="5212", control="bank_charges").update(control="", is_system=False)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_journal")]

    operations = [migrations.RunPython(forwards, backwards)]
