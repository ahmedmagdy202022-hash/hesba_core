from django.core.management.base import BaseCommand

from reports.services import get_local_controlled_cycle_snapshot


class Command(BaseCommand):
    help = "Print a read-only report smoke snapshot for the local controlled cycle."

    def handle(self, *args, **options):
        snapshot = get_local_controlled_cycle_snapshot()

        self.stdout.write("REPORT_SMOKE_SNAPSHOT_OK")
        self.stdout.write(f"Supplier balance: {snapshot['supplier_balance']}")
        self.stdout.write(f"Customer balance: {snapshot['customer_balance']}")
        self.stdout.write(f"Cashbox balance: {snapshot['cashbox_balance']}")
        self.stdout.write(f"Item location stock: {snapshot['item_location_stock']}")
        self.stdout.write(f"Supplier ledger entries: {snapshot['supplier_ledger_entries']}")
        self.stdout.write(f"Customer ledger entries: {snapshot['customer_ledger_entries']}")
        self.stdout.write(f"Cashbox movements: {snapshot['cashbox_movements']}")
        profit = snapshot["profit"]
        self.stdout.write(f"Total sales: {profit['total_sales']}")
        self.stdout.write(f"Total cost: {profit['total_cost']}")
        self.stdout.write(f"Total profit: {profit['total_profit']}")
