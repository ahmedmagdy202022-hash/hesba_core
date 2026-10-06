"""The default Egyptian chart for a small or medium business, per activity.

Rows: (code, Arabic, English, type, control, postable, contra). Group rows
(postable False) are headings. Activity layers rename revenue and cost
accounts and add the ones the activity needs (raw materials and work in
progress for a factory, work in progress on projects for a contractor).
Pure data: the migration and the service both read it.
"""

A, L, E, I, X = "asset", "liability", "equity", "income", "expense"

BASE = (
    ("1", "الأصول", "Assets", A, "", False, False),
    ("11", "الأصول المتداولة", "Current assets", A, "", False, False),
    ("1101", "النقدية بالخزن", "Cash on hand", A, "cash", True, False),
    ("1102", "البنوك", "Banks", A, "bank", True, False),
    ("1103", "العملاء", "Customers (receivable)", A, "receivable", True, False),
    ("1104", "أوراق القبض", "Notes receivable", A, "", True, False),
    ("1105", "المخزون", "Inventory", A, "inventory", True, False),
    ("1106", "ضريبة القيمة المضافة - مدخلات", "VAT input", A, "vat_in", True, False),
    ("1107", "مصروفات مدفوعة مقدمًا", "Prepaid expenses", A, "", True, False),
    ("1108", "أقساط مستحقة على العملاء", "Instalments receivable", A, "instalments", True, False),
    ("1109", "حسابات جارية بين الكيانات", "Due between entities", A, "intercompany", True, False),
    ("1199", "حساب معلّق (فروق تحت المراجعة)", "Suspense (differences under review)", A, "suspense", True, False),
    ("12", "الأصول الثابتة", "Fixed assets", A, "", False, False),
    ("1201", "الأصول الثابتة بالتكلفة", "Fixed assets at cost", A, "fixed_assets", True, False),
    ("1202", "مجمع الإهلاك", "Accumulated depreciation", A, "accumulated_depreciation", True, True),
    ("2", "الخصوم", "Liabilities", L, "", False, False),
    ("21", "الخصوم المتداولة", "Current liabilities", L, "", False, False),
    ("2101", "الموردون", "Suppliers (payable)", L, "payable", True, False),
    ("2102", "أوراق الدفع", "Notes payable", L, "", True, False),
    ("2103", "ضريبة القيمة المضافة - مخرجات", "VAT output", L, "vat_out", True, False),
    ("2104", "مصروفات مستحقة", "Accrued expenses", L, "", True, False),
    ("2105", "دفعات مقدمة من العملاء", "Customer advances", L, "customer_advances", True, False),
    ("22", "الخصوم طويلة الأجل", "Long-term liabilities", L, "", False, False),
    ("2201", "القروض", "Loans", L, "", True, False),
    ("3", "حقوق الملكية", "Equity", E, "", False, False),
    ("31", "رأس المال والحسابات الجارية", "Capital and current accounts", E, "", False, False),
    ("3101", "رأس المال", "Capital", E, "capital", True, False),
    ("3102", "جاري المالك", "Owner's current account", E, "owner_drawings", True, False),
    ("3103", "أرصدة افتتاحية", "Opening balances", E, "opening_equity", True, False),
    ("3104", "الأرباح المحتجزة", "Retained earnings", E, "retained_earnings", True, False),
    ("4", "الإيرادات", "Income", I, "", False, False),
    ("41", "إيرادات النشاط", "Operating revenue", I, "", False, False),
    ("4101", "إيراد المبيعات", "Sales revenue", I, "sales", True, False),
    ("4102", "مردودات المبيعات", "Sales returns", I, "sales_returns", True, True),
    ("4103", "خصم مسموح به", "Discounts allowed", I, "sales_discount", True, True),
    ("42", "إيرادات أخرى", "Other revenue", I, "", False, False),
    ("4201", "إيرادات متنوعة", "Sundry income", I, "other_income", True, False),
    ("4202", "أرباح فروق الجرد", "Stock count gains", I, "stock_gain", True, False),
    ("4203", "أرباح وخسائر بيع الأصول", "Gain or loss on asset disposal", I, "asset_disposal", True, False),
    ("5", "التكاليف والمصروفات", "Costs and expenses", X, "", False, False),
    ("51", "تكلفة المبيعات", "Cost of sales", X, "", False, False),
    ("5101", "تكلفة البضاعة المباعة", "Cost of goods sold", X, "cogs", True, False),
    ("52", "المصروفات التشغيلية", "Operating expenses", X, "", False, False),
    ("5201", "مرتبات وأجور", "Salaries & wages", X, "", True, False),
    ("5202", "إيجار", "Rent", X, "", True, False),
    ("5203", "كهرباء ومياه وغاز", "Utilities", X, "", True, False),
    ("5204", "إنترنت وتليفون", "Internet & phone", X, "", True, False),
    ("5205", "نقل ومواصلات", "Transport", X, "", True, False),
    ("5206", "صيانة وإصلاحات", "Maintenance & repairs", X, "", True, False),
    ("5207", "دعاية وتسويق", "Marketing", X, "", True, False),
    ("5208", "أدوات ومستلزمات", "Supplies", X, "", True, False),
    ("5209", "رسوم وضرائب", "Fees & taxes", X, "", True, False),
    ("5210", "مصروف الإهلاك", "Depreciation expense", X, "depreciation", True, False),
    ("5211", "خسائر فروق الجرد", "Stock count losses", X, "stock_loss", True, False),
    ("5212", "مصروفات بنكية", "Bank charges", X, "", True, False),
    ("5299", "مصروفات عمومية أخرى", "Other general expenses", X, "general_expense", True, False),
)

# Activity layers: rename by code, and add accounts.
RENAME = {
    "services": {"4101": ("إيراد الخدمات", "Service revenue")},
    "medical": {"4101": ("إيراد الكشوفات والخدمات الطبية", "Medical services revenue")},
    "education": {"4101": ("إيراد الاشتراكات والمصروفات الدراسية", "Tuition and fees revenue"), "1103": ("الطلاب (مصروفات مستحقة)", "Students (fees receivable)")},
    "restaurants": {"4101": ("إيراد المطعم", "Restaurant revenue"), "5101": ("تكلفة الأكل والمشروبات", "Food & beverage cost")},
    "contracting": {"4101": ("إيراد المستخلصات", "Progress billing revenue"), "1103": ("أصحاب المشاريع", "Project owners (receivable)")},
}
ADD = {
    "manufacturing": (
        ("110501", "مخزون الخامات", "Raw materials", A, "raw_materials", True, False),
        ("110502", "إنتاج تحت التشغيل", "Work in progress", A, "wip", True, False),
        ("110503", "مخزون المنتج التام", "Finished goods", A, "finished_goods", True, False),
        ("5102", "تكلفة الإنتاج المحمّلة", "Absorbed production cost", X, "production_cost", True, False),
    ),
    "contracting": (
        ("1110", "أعمال تحت التنفيذ", "Work in progress on projects", A, "project_wip", True, False),
        ("5103", "تكلفة المشاريع", "Project costs", X, "project_cost", True, False),
    ),
}

#: Expense category code → account code.
EXPENSE_ACCOUNTS = {
    "salaries": "5201", "rent": "5202", "utilities": "5203", "internet_phone": "5204", "transport": "5205",
    "maintenance": "5206", "marketing": "5207", "supplies": "5208", "fees_taxes": "5209", "other": "5299",
}


def rows_for(activity):
    renamed = RENAME.get(activity, {})
    rows = [(code, *renamed.get(code, (ar, en)), kind, control, postable, contra) for code, ar, en, kind, control, postable, contra in BASE]
    rows += list(ADD.get(activity, ()))
    if activity == "manufacturing":
        # Inventory becomes a heading over raw, WIP and finished stock.
        rows = [(c, ar, en, k, ("" if c == "1105" else ctl), (False if c == "1105" else p), ct) for c, ar, en, k, ctl, p, ct in rows]
    return sorted(rows, key=lambda row: row[0])


def parent_code(code):
    if len(code) <= 1:
        return None
    if len(code) == 2:
        return code[0]
    return code[:-2]
