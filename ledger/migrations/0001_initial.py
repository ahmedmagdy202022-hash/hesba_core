import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("expenses", "0002_seed_default_categories")]

    operations = [
        migrations.CreateModel(
            name="Account",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("account_type", models.CharField(choices=[("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"), ("expense", "Expense")], max_length=20)),
                ("control", models.CharField(blank=True, help_text="What posts here automatically: cash, receivable, sales…", max_length=40)),
                ("is_postable", models.BooleanField(default=True)),
                ("is_contra", models.BooleanField(default=False, help_text="Reduces its group: accumulated depreciation, sales returns…")),
                ("is_system", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="ledger.account")),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.AddConstraint(
            model_name="account",
            constraint=models.UniqueConstraint(condition=models.Q(("control", ""), _negated=True), fields=("control",), name="one_account_per_control"),
        ),
        migrations.CreateModel(
            name="ExpenseAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_categories", to="ledger.account")),
                ("category", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="ledger_link", to="expenses.expensecategory")),
            ],
        ),
    ]
