"""ACT-PROFILE-001: the words each activity uses for the same things.

A clinic has patients and visits, a school has students and fees, a kitchen
has a menu and orders, a contractor has project owners and progress bills.
The records underneath are the same (Customer, Item, SalesInvoice), so this is
display only: one dictionary of terms per activity, with sub-activity
overrides, read by the navigation, the dashboard and the master-data screens.

``terms(lang)`` returns the active installation's vocabulary; ``term(key, lang)``
one word. Keys missing from an activity fall back to the general words, so a
new key never shows blank.
"""

from functools import lru_cache

BASE = {
    "customers": {"ar": "العملاء", "en": "Customers"},
    "customers_short": {"ar": "العملاء", "en": "Customers"},  # the phone tab bar's label; follows "customers" unless an activity gives its own
    "customer": {"ar": "عميل", "en": "Customer"},
    "the_customer": {"ar": "العميل", "en": "Customer"},
    "new_customer": {"ar": "عميل جديد", "en": "New customer"},
    "collect": {"ar": "تحصيل من عميل", "en": "Collect from a customer"},
    "customer_dues": {"ar": "مديونيات العملاء", "en": "Customer dues"},
    "suppliers": {"ar": "الموردون", "en": "Suppliers"},
    "supplier": {"ar": "مورد", "en": "Supplier"},
    "new_supplier": {"ar": "مورد جديد", "en": "New supplier"},
    "items": {"ar": "الأصناف والخدمات", "en": "Items & services"},
    "items_short": {"ar": "الأصناف", "en": "Items"},
    "item": {"ar": "صنف / خدمة", "en": "Item / service"},
    "new_item": {"ar": "صنف / خدمة جديدة", "en": "New item or service"},
    "operations": {"ar": "عمليات البيع", "en": "Sales operations"},
    "operations_short": {"ar": "البيع", "en": "Sales"},
    "record_sale": {"ar": "تسجيل عملية بيع", "en": "Record a sale"},
    "new_sale": {"ar": "فاتورة بيع جديدة", "en": "New sales invoice"},
    "staff": {"ar": "الموظفون", "en": "Employees"},
    "appointments": {"ar": "المواعيد", "en": "Appointments"},
    # WATCH1B (CL-01): the sentences that name the sale or the customer on the dashboard, the invoice form, the customer
    # page and the agenda. Each key holds one whole phrase (never a noun to splice into a sentence), and BASE keeps
    # today's wording exactly: only an activity that lists the key says anything different.
    "dash_sales_today": {"ar": "مبيعات اليوم", "en": "Sales today"},
    "dash_sales_today_own": {"ar": "مبيعاتي اليوم", "en": "My sales today"},
    "dash_hero_today": {"ar": "مبيعات النهارده", "en": "Sales today"},
    "dash_hero_headline": {"ar": "صافي مبيعات {period}", "en": "Net sales · {period}"},
    "dash_hero_trend": {"ar": "اتجاه المبيعات", "en": "Sales trend"},
    "dash_daily_title": {"ar": "المبيعات يوم بيوم", "en": "Sales by day"},
    "dash_value_col": {"ar": "المبيعات", "en": "Sales"},
    "dash_tab_sales": {"ar": "المبيعات", "en": "Sales"},
    "dash_no_sales_chart": {"ar": "مفيش مبيعات في الفترة دي لسه.", "en": "No sales in this period yet."},
    "dash_phero_none": {"ar": "لسه مفيش بيع النهارده؛ أول فاتورة هتظهر هنا.", "en": "No sales yet today; the first invoice shows here."},
    "dash_split_credit": {"ar": "آجل على العملاء", "en": "On customer credit"},
    "dash_top_title_profit": {"ar": "أكتر الأصناف ربحًا", "en": "Most profitable items"},
    "dash_top_title_sales": {"ar": "أكتر الأصناف مبيعًا", "en": "Best-selling items"},
    "dash_hours_peak": {"ar": "أكتر ساعة بيع", "en": "Busiest hour"},
    "dash_weekdays_title": {"ar": "متوسط البيع حسب يوم الأسبوع (آخر 4 أسابيع)", "en": "Average sales by weekday (last 4 weeks)"},
    "dash_split_paid": {"ar": "اتدفع وقت البيع", "en": "Paid at the till"},
    "dash_split_note": {"ar": "من فواتير البيع النهائية في الفترة.", "en": "From posted sales invoices in the period."},
    "dash_split_empty": {"ar": "مفيش فواتير بيع في الفترة دي.", "en": "No sales invoices in this period."},
    "party_type_sales_due": {"ar": "فاتورة بيع آجل", "en": "Credit sale"},
    "party_type_sales_return": {"ar": "مرتجع بيع", "en": "Sales return"},
    "dash_ask_hint": {"ar": "اسأل عن مبيعاتك ومخزونك وخزنتك وعملاءك", "en": "Ask about your sales, stock, cash or customers"},
    "metric_net_sales": {"ar": "صافي المبيعات", "en": "Net sales"},
    "metric_net_sales_how": {
        "ar": "إجمالي فواتير البيع النهائية في الفترة ناقص مرتجعات البيع النهائية في نفس الفترة. المقارنة بفترة قبلها بنفس عدد الأيام.",
        "en": "Posted sales invoices in the period minus posted sales returns in the same period, compared with the previous period of the same length.",
    },
    "metric_avg_invoice": {"ar": "متوسط الفاتورة", "en": "Average invoice"},
    "metric_avg_invoice_how": {"ar": "صافي المبيعات ÷ عدد فواتير البيع النهائية في الفترة.", "en": "Net sales ÷ number of posted sales invoices in the period."},
    "metric_receivables": {"ar": "فلوسك برّه (آجل العملاء)", "en": "Owed to you (customers)"},
    "metric_receivables_how": {
        "ar": "مجموع أرصدة العملاء المدينة. «متأخر» = اللي عدّى مدة الآجل المسموحة، والأقدم بيتسدد الأول.",
        "en": "Sum of customer balances owed to you. Overdue = past the allowed credit days, oldest settled first.",
    },
    "invoice_due_customer": {"ar": "الباقي على العميل", "en": "Left on the customer"},
    "invoice_customer_account": {"ar": "حساب العميل", "en": "Customer account"},
    "back_to_sales": {"ar": "العودة للمبيعات", "en": "Back to sales"},
    "party_owes": {"ar": "مستحق على العميل", "en": "Owed by the customer"},
    "party_in_credit": {"ar": "رصيد دائن للعميل", "en": "Customer in credit"},
    "party_year_total": {"ar": "إجمالي فواتيره السنة دي", "en": "Sales to them this year"},
    "agenda_intro": {
        "ar": "احجز ميعاد في المحل أو زيارة عند العميل، واربطه بالخدمة والموظف. لما الشغل يخلص، اعمل فاتورته بدوسة.",
        "en": "Book a slot at the shop or a visit at the customer's, with the service and the employee. When the work is done, bill it in one click.",
    },
    "agenda_book": {"ar": "احجز ميعاد", "en": "Book an appointment"},
    "agenda_customer": {"ar": "العميل", "en": "Customer"},
    "agenda_service": {"ar": "الخدمة", "en": "Service"},
    "agenda_kind_place": {"ar": "في المحل", "en": "At the shop"},
    "agenda_kind_visit": {"ar": "زيارة عند العميل", "en": "Visit to the customer"},
    "agenda_kind_online": {"ar": "كشف أونلاين", "en": "Online visit"},  # M10 (HG-096): offered in a medical installation only
}

# Activity-wide words. Only what differs from BASE is listed.
ACTIVITY = {
    "medical": {
        "customers": {"ar": "المرضى", "en": "Patients"},
        "customer": {"ar": "مريض", "en": "Patient"},
        "the_customer": {"ar": "المريض", "en": "Patient"},
        "new_customer": {"ar": "مريض جديد", "en": "New patient"},
        "collect": {"ar": "تحصيل من مريض", "en": "Collect from a patient"},
        "customer_dues": {"ar": "مستحقات على المرضى", "en": "Patient balances"},
        "items": {"ar": "الخدمات الطبية والمستلزمات", "en": "Medical services & supplies"},
        "items_short": {"ar": "الخدمات", "en": "Services"},
        "item": {"ar": "خدمة / مستلزم", "en": "Service / supply"},
        "new_item": {"ar": "خدمة طبية جديدة", "en": "New medical service"},
        "operations": {"ar": "الكشوفات والفواتير", "en": "Visits & bills"},
        "operations_short": {"ar": "الكشوفات", "en": "Visits"},
        "record_sale": {"ar": "تسجيل كشف", "en": "Record a visit"},
        "new_sale": {"ar": "كشف / فاتورة جديدة", "en": "New visit bill"},
        "staff": {"ar": "الأطباء والطاقم", "en": "Doctors & staff"},
        "appointments": {"ar": "الحجوزات", "en": "Bookings"},
        # WATCH1B (CL-01): a clinic's takings are its revenue, what it counts are bills of visits, and it is owed by patients.
        "dash_sales_today": {"ar": "إيراد اليوم", "en": "Revenue today"},
        "dash_sales_today_own": {"ar": "إيرادي اليوم", "en": "My revenue today"},
        "dash_hero_today": {"ar": "إيراد النهارده", "en": "Revenue today"},
        "dash_hero_headline": {"ar": "صافي إيراد {period}", "en": "Net revenue · {period}"},
        "dash_hero_trend": {"ar": "اتجاه الإيراد", "en": "Revenue trend"},
        "dash_daily_title": {"ar": "الإيراد يوم بيوم", "en": "Revenue by day"},
        "dash_value_col": {"ar": "الإيراد", "en": "Revenue"},
        "dash_tab_sales": {"ar": "الإيراد", "en": "Revenue"},
        "dash_no_sales_chart": {"ar": "مفيش إيراد في الفترة دي لسه.", "en": "No revenue in this period yet."},
        "dash_phero_none": {"ar": "لسه مفيش إيراد النهارده؛ أول فاتورة هتظهر هنا.", "en": "No revenue yet today; the first bill shows here."},
        "dash_split_credit": {"ar": "آجل على المرضى", "en": "On patient credit"},
        "dash_top_title_profit": {"ar": "أكتر الخدمات ربحًا", "en": "Most profitable services"},
        "dash_top_title_sales": {"ar": "أكتر الخدمات إيرادًا", "en": "Services with the most revenue"},
        "dash_hours_peak": {"ar": "أكتر ساعة زحمة", "en": "Busiest hour"},
        "dash_weekdays_title": {"ar": "متوسط الإيراد حسب يوم الأسبوع (آخر 4 أسابيع)", "en": "Average revenue by weekday (last 4 weeks)"},
        "dash_split_paid": {"ar": "اتدفع وقت الكشف", "en": "Paid at the till"},
        "dash_split_note": {"ar": "من الفواتير النهائية في الفترة.", "en": "From posted invoices in the period."},
        "dash_split_empty": {"ar": "مفيش فواتير في الفترة دي.", "en": "No invoices in this period."},
        "party_type_sales_due": {"ar": "فاتورة آجلة", "en": "Credit bill"},
        "party_type_sales_return": {"ar": "مرتجع فاتورة", "en": "Bill return"},
        "dash_ask_hint": {"ar": "اسأل عن إيرادك ومخزونك وخزنتك ومرضاك", "en": "Ask about your revenue, stock, cash or patients"},
        "metric_net_sales": {"ar": "صافي الإيراد", "en": "Net revenue"},
        "metric_net_sales_how": {
            "ar": "إجمالي الفواتير النهائية في الفترة ناقص المرتجعات النهائية في نفس الفترة. المقارنة بفترة قبلها بنفس عدد الأيام.",
            "en": "Posted invoices in the period minus posted returns in the same period, compared with the previous period of the same length.",
        },
        "metric_avg_invoice": {"ar": "متوسط فاتورة الكشف", "en": "Average visit bill"},
        "metric_avg_invoice_how": {"ar": "صافي الإيراد ÷ عدد الفواتير النهائية في الفترة.", "en": "Net revenue ÷ number of posted invoices in the period."},
        "metric_receivables": {"ar": "فلوسك عند المرضى (آجل المرضى)", "en": "Owed to you (patients)"},
        "metric_receivables_how": {
            "ar": "مجموع أرصدة المرضى المدينة. «متأخر» = اللي عدّى مدة الآجل المسموحة، والأقدم بيتسدد الأول.",
            "en": "Sum of patient balances owed to you. Overdue = past the allowed credit days, oldest settled first.",
        },
        "invoice_due_customer": {"ar": "الباقي على المريض", "en": "Left on the patient"},
        "invoice_customer_account": {"ar": "حساب المريض", "en": "Patient account"},
        "back_to_sales": {"ar": "العودة للكشوفات والفواتير", "en": "Back to visits & bills"},
        "party_owes": {"ar": "مستحق على المريض", "en": "Owed by the patient"},
        "party_in_credit": {"ar": "رصيد دائن للمريض", "en": "Patient in credit"},
        "party_year_total": {"ar": "إجمالي فواتيره السنة دي", "en": "Billed to them this year"},
        "agenda_intro": {
            "ar": "احجز كشف للمريض مع الدكتور، أو زيارة عند المريض. لما الكشف يخلص، اعمل فاتورته بدوسة.",
            "en": "Book a visit for a patient with the doctor, or a visit at the patient's home. When the visit is done, bill it in one click.",
        },
        "agenda_book": {"ar": "احجز كشف", "en": "Book a visit"},
        "agenda_customer": {"ar": "المريض", "en": "Patient"},
        "agenda_service": {"ar": "الكشف", "en": "Visit"},
        "agenda_kind_place": {"ar": "في العيادة", "en": "At the clinic"},
        "agenda_kind_visit": {"ar": "زيارة عند المريض", "en": "Visit to the patient"},
    },
    # EDU-001: the student has their own file (Students & groups); the account,
    # its invoices and its payments belong to whoever pays: the parent.
    "education": {
        "customers": {"ar": "أولياء الأمور", "en": "Parents & payers"},
        "customer": {"ar": "ولي أمر", "en": "Parent"},
        "the_customer": {"ar": "ولي الأمر", "en": "Parent"},
        "new_customer": {"ar": "ولي أمر جديد", "en": "New parent"},
        "collect": {"ar": "تحصيل مصروفات", "en": "Collect fees"},
        "customer_dues": {"ar": "مصروفات متأخرة", "en": "Fees outstanding"},
        "items": {"ar": "الكورسات والمواد", "en": "Courses & materials"},
        "items_short": {"ar": "الكورسات", "en": "Courses"},
        "item": {"ar": "كورس / مادة", "en": "Course / material"},
        "new_item": {"ar": "كورس جديد", "en": "New course"},
        "operations": {"ar": "الاشتراكات والمصروفات", "en": "Enrolments & fees"},
        "operations_short": {"ar": "الاشتراكات", "en": "Fees"},
        "record_sale": {"ar": "تسجيل اشتراك", "en": "Record an enrolment"},
        "new_sale": {"ar": "اشتراك جديد", "en": "New enrolment"},
        "staff": {"ar": "المدرّسون", "en": "Teachers"},
        "appointments": {"ar": "الحصص والمواعيد", "en": "Classes"},
    },
    "restaurants": {
        "items": {"ar": "المنيو", "en": "Menu"},
        "items_short": {"ar": "المنيو", "en": "Menu"},
        "item": {"ar": "صنف في المنيو", "en": "Menu item"},
        "new_item": {"ar": "صنف جديد في المنيو", "en": "New menu item"},
        "operations": {"ar": "الطلبات والفواتير", "en": "Orders & bills"},
        "operations_short": {"ar": "الطلبات", "en": "Orders"},
        "record_sale": {"ar": "تسجيل طلب", "en": "Record an order"},
        "new_sale": {"ar": "طلب جديد", "en": "New order"},
        "staff": {"ar": "الطاقم", "en": "Crew"},
    },
    "manufacturing": {
        "customers": {"ar": "العملاء والموزعون", "en": "Customers & distributors"},
        "items": {"ar": "الخامات والمنتجات", "en": "Materials & products"},
        "items_short": {"ar": "الأصناف", "en": "Items"},
        "item": {"ar": "خامة / منتج", "en": "Material / product"},
        "new_item": {"ar": "خامة أو منتج جديد", "en": "New material or product"},
        "suppliers": {"ar": "موردو الخامات", "en": "Material suppliers"},
        "staff": {"ar": "العمال والفنيون", "en": "Workers & technicians"},
    },
    "contracting": {
        "customers": {"ar": "أصحاب المشاريع", "en": "Project owners"},
        "customer": {"ar": "صاحب مشروع", "en": "Project owner"},
        "the_customer": {"ar": "صاحب المشروع", "en": "Project owner"},
        "new_customer": {"ar": "صاحب مشروع جديد", "en": "New project owner"},
        "collect": {"ar": "تحصيل دفعة", "en": "Collect a payment"},
        "customer_dues": {"ar": "مستحقات على أصحاب المشاريع", "en": "Owner balances"},
        "suppliers": {"ar": "الموردون ومقاولو الباطن", "en": "Suppliers & subcontractors"},
        "new_supplier": {"ar": "مورد / مقاول باطن جديد", "en": "New supplier or subcontractor"},
        "items": {"ar": "المواد والبنود", "en": "Materials & work items"},
        "items_short": {"ar": "المواد", "en": "Materials"},
        "item": {"ar": "مادة / بند", "en": "Material / work item"},
        "new_item": {"ar": "مادة أو بند جديد", "en": "New material or work item"},
        "operations": {"ar": "المستخلصات والفواتير", "en": "Progress bills & invoices"},
        "operations_short": {"ar": "المستخلصات", "en": "Bills"},
        "record_sale": {"ar": "تسجيل مستخلص / فاتورة", "en": "Record a bill"},
        "new_sale": {"ar": "مستخلص / فاتورة جديدة", "en": "New progress bill or invoice"},
        "staff": {"ar": "المهندسون والعمال", "en": "Engineers & crew"},
    },
    "services": {
        "items": {"ar": "الخدمات", "en": "Services"},
        "items_short": {"ar": "الخدمات", "en": "Services"},
        "item": {"ar": "خدمة", "en": "Service"},
        "new_item": {"ar": "خدمة جديدة", "en": "New service"},
        "operations": {"ar": "الفواتير", "en": "Invoices"},
        "operations_short": {"ar": "الفواتير", "en": "Invoices"},
        "record_sale": {"ar": "تسجيل فاتورة خدمة", "en": "Record a service invoice"},
        "new_sale": {"ar": "فاتورة خدمة جديدة", "en": "New service invoice"},
        "staff": {"ar": "الفنيون والموظفون", "en": "Technicians & staff"},
    },
}

# Sub-activity refinements, layered over the activity's words.
SUB_ACTIVITY = {
    ("commercial", "pharmacy"): {
        "items": {"ar": "الأدوية والأصناف", "en": "Medicines & items"},
        "item": {"ar": "دواء / صنف", "en": "Medicine / item"},
        "new_item": {"ar": "دواء أو صنف جديد", "en": "New medicine or item"},
    },
    ("commercial", "wholesale"): {
        "customers": {"ar": "العملاء والتجار", "en": "Customers & traders"},
    },
    # SVC-001: the work orders have their own section now; sales is where their invoices land.
    ("services", "maintenance"): {
        "operations": {"ar": "الفواتير والتحصيل", "en": "Invoices & payments"},
        "operations_short": {"ar": "الفواتير", "en": "Invoices"},
        "record_sale": {"ar": "فاتورة مباشرة", "en": "Direct invoice"},
    },
    ("services", "clinic"): ACTIVITY["medical"],
    # A training business set up as a service has no student files: the learner is the account.
    ("services", "education"): {**ACTIVITY["education"], **{
        "customers": {"ar": "الطلاب", "en": "Students"},
        "customer": {"ar": "طالب", "en": "Student"},
        "the_customer": {"ar": "الطالب", "en": "Student"},
        "new_customer": {"ar": "طالب جديد", "en": "New student"},
    }},
    ("services", "beauty"): {
        "staff": {"ar": "الطاقم والخبيرات", "en": "Stylists & staff"},
        "appointments": {"ar": "الحجوزات", "en": "Bookings"},
    },
    ("services", "professional"): {
        "customers": {"ar": "الموكّلون والعملاء", "en": "Clients"},
        "items": {"ar": "الخدمات والأتعاب", "en": "Services & fees"},
    },
    ("medical", "vet"): {
        "customers": {"ar": "أصحاب الحيوانات", "en": "Pet owners"},
        "customer": {"ar": "صاحب حيوان", "en": "Pet owner"},
        "the_customer": {"ar": "صاحب الحيوان", "en": "Pet owner"},
        "new_customer": {"ar": "صاحب حيوان جديد", "en": "New pet owner"},
        "collect": {"ar": "تحصيل من عميل", "en": "Collect from a client"},
        "dash_split_credit": {"ar": "آجل على أصحاب الحيوانات", "en": "On pet-owner credit"},
        "dash_ask_hint": {"ar": "اسأل عن إيرادك ومخزونك وخزنتك وأصحاب الحيوانات", "en": "Ask about your revenue, stock, cash or pet owners"},
        "metric_receivables": {"ar": "فلوسك عند أصحاب الحيوانات (آجل)", "en": "Owed to you (pet owners)"},
        "metric_receivables_how": {
            "ar": "مجموع أرصدة أصحاب الحيوانات المدينة. «متأخر» = اللي عدّى مدة الآجل المسموحة، والأقدم بيتسدد الأول.",
            "en": "Sum of pet-owner balances owed to you. Overdue = past the allowed credit days, oldest settled first.",
        },
        "invoice_due_customer": {"ar": "الباقي على صاحب الحيوان", "en": "Left on the pet owner"},
        "invoice_customer_account": {"ar": "حساب صاحب الحيوان", "en": "Pet owner account"},
        "party_owes": {"ar": "مستحق على صاحب الحيوان", "en": "Owed by the pet owner"},
        "party_in_credit": {"ar": "رصيد دائن لصاحب الحيوان", "en": "Pet owner in credit"},
        "agenda_intro": {
            "ar": "احجز كشف لحيوان مع الدكتور البيطري، أو زيارة عند صاحب الحيوان. لما الكشف يخلص، اعمل فاتورته بدوسة.",
            "en": "Book a visit for a pet with the vet, or a visit at the pet owner's. When the visit is done, bill it in one click.",
        },
        "agenda_customer": {"ar": "صاحب الحيوان", "en": "Pet owner"},
        "agenda_kind_place": {"ar": "في العيادة البيطرية", "en": "At the vet clinic"},
        "agenda_kind_visit": {"ar": "زيارة عند صاحب الحيوان", "en": "Visit to the pet owner"},
    },
    ("medical", "lab"): {
        "operations": {"ar": "طلبات التحاليل والفواتير", "en": "Test orders & bills"},
        "operations_short": {"ar": "التحاليل", "en": "Tests"},
        "record_sale": {"ar": "تسجيل طلب تحليل", "en": "Record a test order"},
        "agenda_kind_place": {"ar": "في المعمل", "en": "At the lab"},
    },
    ("medical", "medical_center"): {"agenda_kind_place": {"ar": "في المركز", "en": "At the centre"}},
    ("medical", "physio"): {"agenda_kind_place": {"ar": "في المركز", "en": "At the centre"}},
    ("education", "nursery"): {
        "staff": {"ar": "المشرفات والطاقم", "en": "Carers & staff"},
    },
    # Adults mostly pay for themselves.
    ("education", "training"): {
        "customers": {"ar": "حسابات المتدربين", "en": "Trainee accounts"},
        "customers_short": {"ar": "المتدربين", "en": "Trainees"},  # ED-09: the tab bar's label; the page keeps the full name
        "customer": {"ar": "متدرب", "en": "Trainee"},
        "the_customer": {"ar": "المتدرب", "en": "Trainee"},
        "new_customer": {"ar": "حساب متدرب جديد", "en": "New trainee account"},
    },
    ("education", "languages"): {
        "customers": {"ar": "حسابات الدارسين", "en": "Learner accounts"},
        "customers_short": {"ar": "الدارسين", "en": "Learners"},
        "customer": {"ar": "دارس", "en": "Learner"},
        "the_customer": {"ar": "الدارس", "en": "Learner"},
        "new_customer": {"ar": "حساب دارس جديد", "en": "New learner account"},
    },
}


@lru_cache(maxsize=64)
def vocabulary(activity, sub_activity):
    """The merged term table for one (activity, sub-activity)."""

    merged = {key: dict(value) for key, value in BASE.items()}
    layers = (ACTIVITY.get(activity, {}), SUB_ACTIVITY.get((activity, sub_activity), {}))
    for layer in layers:
        for key, value in layer.items():
            merged[key] = dict(value)
    if not any("customers_short" in layer for layer in layers):  # ED-09: no short label of its own: the tab uses the full word
        merged["customers_short"] = dict(merged["customers"])
    return merged


def active_vocabulary():
    # ENT-002: the words of the entity being worked in, else the installation's.
    from entities.current import effective_activity

    activity, sub_activity = effective_activity()
    return vocabulary(activity or "", sub_activity or "")


def terms(lang, table=None):
    """{key: word} in one language."""

    lang = "en" if lang == "en" else "ar"
    table = table or active_vocabulary()
    return {key: value[lang] for key, value in table.items()}


def term(key, lang, table=None):
    table = table or active_vocabulary()
    entry = table.get(key) or BASE.get(key)
    if entry is None:
        return key
    return entry["en" if lang == "en" else "ar"]
