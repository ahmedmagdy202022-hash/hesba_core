"""AI demo (HG-078 deviation 7, HG-079 to HG-081): what a showcase visitor needs to try the three AI features.

On a showcase install the AI layer answers with its mock (no key, no call out; every answer is labelled as a sample,
``ai.sample``). This module only adds the data that makes those answers worth seeing:

* A1 (voice summary) reads yesterday's collections: the sample month has sales every day but collections only today, so
  one collection from the customer who owes the most is dated yesterday.
* A2 («اسأل حسبة») already has today's sales and collections, low and sold-out items, debtors and cashboxes from the
  sample month; nothing is added for it.
* A3 (invoice photo) reads the mock's fixed sample invoice (``ai.providers.mock.SAMPLE_READING``): the supplier
  «مؤسسة الأمل للتوريدات» and two of its lines («زيت محرك 4 لتر», «فلتر زيت تويوتا») match records added here by their
  exact names; the third line («تيل فرامل أمامي K-22») matches nothing on purpose, so the visitor maps it by hand to the
  shop's own «تيل فرامل أمامي (كود BP-22)». The three parts were bought once before, on credit, so they are in stock and
  never show as sold out.

Everything goes through the real services: the purchase is posted by ``purchases.services.post_purchase_invoice`` (the
demo seed's own ``_post_purchase``) and the collection by ``sales.services.record_customer_payment``. Nothing here writes a
posting, a ledger or a cashbox row by hand.

Only when the capability is on (``ai_invoice_photo``, with purchases on, for the A3 records; ``ai_voice_summary`` for the
collection), and only once: the item ``DEMO-AI-01`` and the payment ``DEMO-CR-AI`` say each part is already there.
A pharmacy gets no car parts (R1: they showed in its till search and drug lists). Its invoice-photo sample is a medicine
distributor's (``PHARMACY_SAMPLE_READING``) and needs nothing seeded here: the supplier «الشركة المتحدة لتوزيع الأدوية»
and two of its lines («باراسيتامول 500 مجم», «فيتامين سي 1000 فوار») are the pharmacy demo's own records, matched by
exact name; the third line («شراب كحة أطفال 100 مل») matches nothing on purpose, so the visitor maps it by hand to the
shop's «شراب كحة للأطفال 100 مل» (``settings_core.tests_demo_ai`` pins all of that).
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

#: The showcase's AI capabilities, switched on with the rest of the demo's extras (prepare_demo, switch_activity).
CAPABILITIES = ("ai_voice_summary", "ai_ask", "ai_invoice_photo")

SENTINEL = "DEMO-AI-01"
SUPPLIER_CODE = "DEMO-SUP-AI"
#: Exactly the mock reading's supplier name (``SAMPLE_READING["supplier_name"]``).
SUPPLIER_NAME = "مؤسسة الأمل للتوريدات"
CATEGORY = ("DEMO-CAT-AI", "قطع غيار عربيات", "Car parts")
#: (code, name, unit, sale price, purchase price, quantity bought before). The first two names are the mock reading's
#: own; the third is the shop's name for the reading's «تيل فرامل أمامي K-22», which must be mapped by hand.
ITEMS = (
    (SENTINEL, "زيت محرك 4 لتر", "جركن", "450.00", "385.00", 6),
    ("DEMO-AI-02", "فلتر زيت تويوتا", "قطعة", "120.00", "95.00", 10),
    ("DEMO-AI-03", "تيل فرامل أمامي (كود BP-22)", "طقم", "300.00", "240.00", 4),
)
#: The earlier purchase from the supplier, so the parts are in stock (and the supplier has a history).
EARLIER_PURCHASE = "DEMO-PI-AI"
BOUGHT_DAYS_AGO = 20
COLLECTION_NUMBER = "DEMO-CR-AI"


def seed(master, owner, modules=()):
    """Add the AI showcase once. Returns True when it was added, False when it was already there or nothing applies.
    ``modules`` are the sections switched on: the A3 records need purchases."""

    from master_data.models import Item
    from sales.models import CustomerPayment
    from settings_core.capabilities import capability_enabled

    photo = (capability_enabled("ai_invoice_photo") and "purchases" in modules and not pharmacy()
             and not Item.objects.filter(item_code=SENTINEL).exists())
    collection = capability_enabled("ai_voice_summary") and not CustomerPayment.objects.filter(payment_number=COLLECTION_NUMBER).exists()
    if not (photo or collection):
        return False
    with transaction.atomic():
        if photo:
            _invoice_photo_records(master, owner)
        if collection:
            _yesterday_collection(master, owner)
    return True


def pharmacy():
    """True when the active profile is a pharmacy. R1: its shelf is drugs, so the car parts of the invoice-photo sample
    stay out of it, and the AI layer's invoice-photo mock reads (and offers) a medicine invoice instead."""

    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return profile is not None and profile.sub_activity_slug == "pharmacy"


def _invoice_photo_records(master, owner):
    from master_data.models import Category, Item, Supplier
    from reports.management.commands.seed_demo_business import Command as Seed

    code, name_ar, name_en = CATEGORY
    category = Category.objects.update_or_create(category_code=code, defaults={"name_ar": name_ar, "name_en": name_en, "active": True})[0]
    supplier = Supplier.objects.get_or_create(supplier_code=SUPPLIER_CODE, defaults={
        "name": SUPPLIER_NAME, "active": True, "import_batch_id": "DEMO-BUSINESS"})[0]
    lines = []
    for item_code, name, unit, sale, purchase, quantity in ITEMS:
        item = Item.objects.create(item_code=item_code, item_name=name, category=category, unit=unit, default_sale_price=Decimal(sale),
                                   default_purchase_price=Decimal(purchase), min_stock=Decimal("0"), is_stock_tracked=True, active=True,
                                   import_batch_id="DEMO-BUSINESS")
        lines.append((item, Decimal(quantity)))
    Seed()._post_purchase(EARLIER_PURCHASE, timezone.localdate() - timedelta(days=BOUGHT_DAYS_AGO), supplier, master["location"],
                          master["cashboxes"][0], lines, Decimal("0.00"), owner)


def _yesterday_collection(master, owner):
    """A quarter of what the customer who owes the most owes (in whole fifties, at least 100), collected yesterday."""

    from reports.aging import aging_rows
    from sales.services import record_customer_payment

    yesterday = timezone.localdate() - timedelta(days=1)
    rows = sorted((row for row in aging_rows("customers", yesterday) if row["total"] > 0), key=lambda row: (-row["total"], row["name"]))
    if not rows:
        return
    amount = max(Decimal("100"), (rows[0]["total"] / 4 / 50).quantize(Decimal("1")) * 50)
    if amount > rows[0]["total"]:
        return
    record_customer_payment(payment_number=COLLECTION_NUMBER, payment_date=yesterday, customer=rows[0]["party"], cashbox=master["cashboxes"][0],
                            amount=amount, user=owner, notes="")
