"""FB-27: the actions the owner can hold for a second person's approval.

Each one is a cancellation or reversal of a posted document, sent from its own screen with a reason (and, for some,
a reversal date). ``run`` calls the same service that screen calls, in the requester's name.
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable


@dataclass(frozen=True)
class Action:
    code: str
    ar: str
    en: str
    url: Callable[[int], str]
    run: Callable
    model: str  # "app.Model" of the document
    scope: str  # its entities.scope path, so a request is decided only where the document can be seen

    def visible(self, object_id):
        from django.apps import apps

        from entities import scope as entity_scope

        model = apps.get_model(self.model)
        return entity_scope.scope(model.objects, getattr(entity_scope, self.scope)).filter(pk=object_id).exists()


def _day(data):
    return date.fromisoformat(data["reversal_date"])


def _cancel_sales_invoice(request):
    from sales.services import cancel_posted_sales_invoice

    cancel_posted_sales_invoice(request.object_id, request.requested_by, request.data.get("reason", ""))


def _cancel_purchase_invoice(request):
    from purchases.services import cancel_posted_purchase_invoice

    cancel_posted_purchase_invoice(request.object_id, request.requested_by, request.data.get("reason", ""))


def _cancel_customer_payment(request):
    from sales.services import cancel_customer_payment

    cancel_customer_payment(request.object_id, request.requested_by, request.data.get("reason", ""))


def _cancel_supplier_payment(request):
    from purchases.services import cancel_supplier_payment

    cancel_supplier_payment(request.object_id, request.requested_by, request.data.get("reason", ""))


def _reverse_sales_return(request):
    from sales.services import cancel_sales_return

    cancel_sales_return(request.object_id, reversal_date=_day(request.data), reason=request.data.get("reason", ""), user=request.requested_by)


def _reverse_purchase_return(request):
    from purchases.services import cancel_purchase_return

    cancel_purchase_return(request.object_id, reversal_date=_day(request.data), reason=request.data.get("reason", ""), user=request.requested_by)


def _cancel_expense(request):
    from expenses.services import cancel_expense

    cancel_expense(request.object_id, reversal_date=_day(request.data), reason=request.data.get("reason", ""), user=request.requested_by)


def _reverse_stock_operation(request):
    from inventory.services import cancel_stock_operation

    cancel_stock_operation(request.object_id, reversal_date=_day(request.data), reason=request.data.get("reason", ""), user=request.requested_by)


def _reverse_cash_operation(request):
    from cashboxes.services import cancel_cashbox_operation

    cancel_cashbox_operation(request.object_id, reversal_date=_day(request.data), reason=request.data.get("reason", ""), user=request.requested_by)


ACTIONS = {action.code: action for action in (
    Action("sales.cancel_invoice", "إلغاء فاتورة بيع مترحّلة", "Cancel a posted sales invoice", lambda pk: f"/sales/{pk}/", _cancel_sales_invoice, "sales.SalesInvoice", "SALES_INVOICE"),
    Action("sales.reverse_return", "عكس مرتجع مبيعات", "Reverse a sales return", lambda pk: f"/sales/returns/{pk}/", _reverse_sales_return, "sales.SalesReturn", "SALES_RETURN"),
    Action("sales.cancel_payment", "إلغاء تحصيل من عميل", "Cancel a customer collection", lambda pk: "/sales/collections/", _cancel_customer_payment, "sales.CustomerPayment", "CUSTOMER_PAYMENT"),
    Action("purchases.cancel_invoice", "إلغاء فاتورة شراء مترحّلة", "Cancel a posted purchase invoice", lambda pk: f"/purchases/{pk}/", _cancel_purchase_invoice, "purchases.PurchaseInvoice", "PURCHASE_INVOICE"),
    Action("purchases.reverse_return", "عكس مرتجع مشتريات", "Reverse a purchase return", lambda pk: f"/purchases/returns/{pk}/", _reverse_purchase_return, "purchases.PurchaseReturn", "PURCHASE_RETURN"),
    Action("purchases.cancel_payment", "إلغاء دفعة لمورد", "Cancel a supplier payment", lambda pk: "/purchases/payments/", _cancel_supplier_payment, "purchases.SupplierPayment", "SUPPLIER_PAYMENT"),
    Action("expenses.cancel", "إلغاء مصروف", "Cancel an expense", lambda pk: "/expenses/", _cancel_expense, "expenses.Expense", "EXPENSE"),
    Action("inventory.reverse_operation", "عكس تسوية أو تحويل مخزون", "Reverse a stock adjustment or transfer", lambda pk: "/inventory/operations/", _reverse_stock_operation, "inventory.StockOperation", "STOCK_OPERATION"),
    Action("cashboxes.reverse_operation", "عكس حركة خزنة", "Reverse a cashbox operation", lambda pk: "/cashboxes/operations/", _reverse_cash_operation, "cashboxes.CashboxOperation", "CASHBOX_OPERATION"),
)}


def label(code, lang="ar"):
    action = ACTIONS.get(code)
    return (action.en if lang == "en" else action.ar) if action else code
