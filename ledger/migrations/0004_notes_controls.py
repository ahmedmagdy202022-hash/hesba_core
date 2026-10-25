"""T4 (HG-056): the default notes receivable (1104) and notes payable (2102) accounts become the ones cheques post to.

As with bank charges (0003): ``ensure_chart`` never changes an existing account, so only the default accounts,
still under their default names, take the keys; a renamed one is left alone and the chart adds its own beside it.
"""

from django.db import migrations

ACCOUNTS = (("1104", "Notes receivable", "notes_receivable"), ("2102", "Notes payable", "notes_payable"))


def forwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    for code, name, control in ACCOUNTS:
        if not Account.objects.filter(control=control).exists():
            Account.objects.filter(code=code, control="", name_en=name).update(control=control, is_system=True)


def backwards(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    for code, _name, control in ACCOUNTS:
        Account.objects.filter(code=code, control=control).update(control="", is_system=False)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0003_bank_charges_control")]

    operations = [migrations.RunPython(forwards, backwards)]
