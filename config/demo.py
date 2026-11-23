"""DEMO-001: what templates need to know about a showcase install."""

from django.conf import settings


def demo_context(request):
    if not getattr(settings, "DEMO_MODE", False):
        return {}
    from .demo_sandbox import enabled

    return {"demo_mode": True, "demo_password": settings.DEMO_PASSWORD, "demo_sandboxes": enabled()}


# ACT-PROFILE-002: on a showcase install the owner can flip the activity to see
# how the words, the menu and the dashboard change. Never on a real install.
# DEMO-FIRST-RUN: each entry has a pre-built, populated template (config/demo_templates.py); the landing page
# offers exactly these. One more entry costs one more ~5 MB file and ~10 s of build.
DEMO_ACTIVITIES = (
    ("commercial", "retail"), ("commercial", "pharmacy"), ("commercial", "grocery"), ("commercial", "fashion"), ("commercial", "electronics"),
    ("restaurants", "restaurant"), ("medical", "clinic"),
    ("education", "training"), ("services", "maintenance"), ("services", "beauty"), ("manufacturing", "food"), ("manufacturing", "furniture"), ("contracting", "general"),
)
#: What a visitor who is not sure opens: a general retail shop.
DEMO_DEFAULT = ("commercial", "retail")

#: DEMO-FIRST-RUN: the landing page's tiles, in order: (activity, Arabic hint, English hint). "other" opens the default shop.
DEMO_TILES = (
    ("commercial", "تجزئة، صيدلية، سوبر ماركت", "Retail, pharmacy, supermarket"),
    ("services", "مركز صيانة، صالون", "Service centre, salon"),
    ("medical", "عيادة بمواعيد وملفات", "A clinic with appointments and files"),
    ("education", "كورسات وطلبة ومصاريف", "Courses, students and fees"),
    ("restaurants", "طاولات وطلبات ومطبخ", "Tables, kitchen and orders"),
    ("contracting", "مشاريع ومستخلصات", "Projects and progress bills"),
    ("manufacturing", "وصفات وأوامر إنتاج", "Recipes and production orders"),
    ("other", "نشاط تجاري عام: بيع وشراء ومخزون", "A general shop that buys, sells and stocks"),
)
#: One line under each sub-activity on the second screen.
DEMO_HINTS = {
    ("commercial", "retail"): ("أي نشاط بيبيع قطاعي", "Any shop that sells retail"),
    ("commercial", "pharmacy"): ("صلاحيات وبدائل وروشتات", "Expiry dates, alternatives, prescriptions"),
    ("commercial", "grocery"): ("باركود وميزان وكرتونة", "Barcodes, scale and cartons"),
    ("commercial", "fashion"): ("مقاسات وألوان", "Sizes and colours"),
    ("commercial", "electronics"): ("سيريال وضمان", "Serial numbers and warranty"),
    ("restaurants", "restaurant"): ("طاولات وطلبات ومطبخ", "Tables, orders and the kitchen"),
    ("medical", "clinic"): ("مواعيد وكشوفات وملفات", "Appointments, visits and patient files"),
    ("education", "training"): ("مجموعات وحضور ومصاريف", "Groups, attendance and fees"),
    ("services", "maintenance"): ("أوامر شغل وفنيين", "Work orders and technicians"),
    ("services", "beauty"): ("مواعيد وباقات جلسات", "Appointments and session packages"),
    ("manufacturing", "food"): ("وصفات وتشغيلات وصلاحية", "Recipes, batches and expiry"),
    ("manufacturing", "furniture"): ("عروض أسعار بمواصفات وأوامر إنتاج خاصة", "Quotations with specifications and custom production orders"),
    ("contracting", "general"): ("مقايسات ومستخلصات", "Bills of quantities and progress bills"),
}

#: What each demo business is called inside an English sentence ("your pharmacy", "a repair workshop"); the
#: catalogue's English labels are titles ("General building", "Courses & training") and read badly mid-sentence.
DEMO_NOUNS_EN = {
    ("commercial", "retail"): "retail shop", ("commercial", "pharmacy"): "pharmacy", ("commercial", "grocery"): "supermarket",
    ("commercial", "fashion"): "clothing store", ("commercial", "electronics"): "mobile and electronics shop",
    ("restaurants", "restaurant"): "restaurant", ("medical", "clinic"): "clinic", ("education", "training"): "training centre",
    ("services", "maintenance"): "repair workshop", ("services", "beauty"): "beauty salon", ("manufacturing", "food"): "food factory", ("manufacturing", "furniture"): "furniture factory",
    ("contracting", "general"): "contracting firm",
}


def demo_subs(activity):
    """The sub-activities the demo offers for ``activity``, in landing order."""

    return [sub for act, sub in DEMO_ACTIVITIES if act == activity]


#: DEMO-FIRST-RUN: «جرّب دلوقتي», three steps in the trade's own words (four for the clinic, M8): (title_ar, title_en, hint_ar, hint_en, url name).
#: Keyed by (activity, sub) with the activity as the fallback; every link is a real screen the owner may open.
DEMO_TOURS = {
    "commercial": (
        ("سجّل عملية بيع من الكاشير", "Ring up a sale at the till", "اكتب اسم صنف أو امسح الباركود؛ الوردية مفتوحة", "Type an item or scan a barcode; the shift is open", "sales:pos"),
        ("شوف اللي قرب يخلص من المخزن", "See what is running low", "صنف خلص وصنف قرب، وتنبيه عليهم", "One item is out and one is low, with an alert", "reports:inventory"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "pharmacy"): (
        ("سجّل عملية بيع من الكاشير", "Ring up a sale at the till", "اكتب اسم دوا أو امسح الباركود؛ الوردية مفتوحة", "Type a medicine or scan a barcode; the shift is open", "sales:pos"),
        ("شوف الأدوية اللي قربت تنتهي صلاحيتها", "See the medicines about to expire", "تشغيلات بتاريخ صلاحية، وفيها اللي محتاج قرار", "Batches with expiry dates, and what needs a decision", "batches:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "grocery"): (
        ("سجّل عملية بيع من الكاشير", "Ring up a sale at the till", "امسح الباركود أو اكتب الوزن", "Scan a barcode or type the weight", "sales:pos"),
        ("الأصناف بالكرتونة وبالوزن", "Items by the carton and by weight", "الرف كله بوحداته وصلاحيته", "The whole shelf with its units and expiry", "master_data:items"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "fashion"): (
        ("سجّل عملية بيع من الكاشير", "Ring up a sale at the till", "اختار المقاس واللون وإنت بتبيع", "Pick the size and colour as you sell", "sales:pos"),
        ("المقاسات والألوان في المخزن", "Sizes and colours in stock", "كل موديل بمقاساته وألوانه", "Each style with its sizes and colours", "variants:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    ("commercial", "electronics"): (
        ("سجّل عملية بيع بالسيريال", "Sell a device by its serial", "الكاشير بيسأل عن السيريال وبيسجّل الضمان", "The till asks for the serial and records the warranty", "sales:pos"),
        ("الأجهزة اللي عندك بسيريالاتها", "Your devices by serial number", "كل جهاز بسيرياله وضمانه", "Every device with its serial and warranty", "serials:index"),
        ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
    ),
    # Restaurants close-out: twelve steps, one for each merged feature; the ones that belong to one record open the demo's own (STEPS_OF_A_RECORD).
    "restaurants": (
        ("افتح طاولة وسجّل طلب", "Open a table and take an order", "الصالة قدامك: اضغط على طاولة وضيف الأطباق", "The floor is in front of you: tap a table, add dishes", "restaurant:board"),
        ("طاولة بإضافات ووجبة كومبو", "A table with add-ons and a combo", "جبنة زيادة، اختيار إجباري على الطبق، ووجبة كومبو في نفس الطلب", "Extra cheese, a required choice on a dish, and a combo on the same order", "restaurant:order_combo"),
        ("قسّم الحساب وادمج طاولتين", "Split a bill and merge two tables", "طاولة اتدمجت مع جارتها، وجزء منها اتدفع لوحده بالأصناف", "A table merged with its neighbour, and a part of it paid on its own by items", "restaurant:order_split"),
        ("الشيك قبل الدفع بالخدمة والكوبون", "The check before paying, with service and a coupon", "نسبة الخدمة والحد الأدنى للفرد وخصم الكوبون، وتقدر تدفع بأكثر من طريقة", "The service charge, the minimum per guest and the coupon, and you can pay several ways", "restaurant:check"),
        ("منيو الـ QR على الطاولة", "The table's QR menu", "اللي بيشوفه الضيف: المنيو وطلب من الطاولة، وطلب مستني قبول الكاشير", "What the guest sees: the menu and ordering from the table, with a request waiting for staff", "restaurant:table_menu"),
        ("حجز الطاولات والعربون", "Table reservations and the deposit", "حجوزات اليوم والبكرة، وحجز عليه عربون", "Today's and tomorrow's bookings, one with a deposit", "restaurant:reservations"),
        ("طلبات مسبقة بميعاد استلام", "Pre-orders with a pick-up time", "طلب بعربون وقائمة الإنتاج لليوم اللي بعده", "An order with a deposit and the production list for the next day", "restaurant:preorders"),
        ("مركز الاتصال: العميل برقمه", "The call centre: the caller by number", "عناوينه المحفوظة ومنطقة التوصيل وآخر طلباته، وكرر الطلب بضغطة", "Saved addresses, the delivery zone and last orders, and repeat an order in one tap", "restaurant:call"),
        ("طلبات المنصات (طلبات وelmenus)", "Delivery-platform orders (Talabat and elmenus)", "طلب اتسجّل على حساب المنصة، وعمولتها، والمبلغ المعلّق عندها", "An order posted on the platform's account, its commission, and what it still holds", "restaurant:platforms"),
        ("أكثر من براند من مطبخ واحد", "Several brands from one kitchen", "كل براند بمنيو وعنوان على الإيصال، والطلب بيحمل اسم براند", "Each brand with its own menu and receipt header; each order carries its brand", "restaurant:brands"),
        ("تكلفة الطبق والتحضير الملغي", "What a dish costs, and a cancelled preparation", "مكونات الطبق وهامشه، وتحضير اتلغى وخاماته رجعت للمخزن", "A dish's ingredients and margin, and a preparation cancelled with its materials back in stock", "restaurant:dish"),
        ("تقرير المطعم النهارده", "Today's restaurant report", "الأطباق الأكثر طلبًا، وساعات الذروة، وأداء فريق الخدمة", "Top dishes, peak hours and the waiters", "restaurant:report"),
    ),
    "medical": (
        ("افتح أجندة النهارده وسجّل كشف", "Open today's agenda and record a visit", "مواعيد النهارده، ومن الموعد تسجّل الكشف", "Today's appointments; record the visit from the appointment", "appointments:agenda"),
        ("افتح ملف مريض", "Open a patient file", "التاريخ المرضي والحساسية والكشوفات", "History, allergies and past visits", "medical:patients"),
        ("قائمة الانتظار", "The waiting list", "مين مستني ومين دخل", "Who is waiting and who went in", "medical:queue"),
    ),
    "education": (
        ("الطلبة والمجموعات", "Students and groups", "كل مجموعة بطلبتها ومدرّسها", "Each group with its students and teacher", "education:students"),
        ("حصص النهارده والغياب", "Today's classes and attendance", "علّم الحضور من الموبايل", "Mark attendance from the phone", "education:today"),
        ("المصاريف المستحقة", "Fees due", "مين دفع ومين عليه، وتذكير بالواتساب", "Who paid, who owes, and a WhatsApp reminder", "education:dues"),
    ),
    "services": (
        ("افتح أمر شغل", "Open a work order", "استلم الجهاز، صوّره، وابعت عرض السعر", "Take the device in, photograph it, send a quote", "workorders:list"),
        ("الفنيين وشغلهم", "The technicians and their work", "كل فني وأوامره وعمولته", "Each technician, their orders and commission", "staff:list"),
        ("تقرير أوامر الشغل", "The work orders report", "اللي خلص واللي مستني، وربح كل أمر", "Done and waiting, and each order's margin", "workorders:report"),
    ),
    ("services", "beauty"): (
        ("احجز ميعاد", "Book an appointment", "الأجندة بمواعيد النهارده", "The agenda with today's appointments", "appointments:agenda"),
        ("باقات الجلسات", "Session packages", "باقة مدفوعة وجلساتها اللي اتخصمت", "A paid package and the sessions used", "agreements:packages"),
        ("سجّل عملية بيع من الكاشير", "Ring up a sale at the till", "منتجات وخدمات، كاش أو فيزا", "Products and services, cash or card", "sales:pos"),
    ),
    # Manufacturing F14: ten steps in the order a factory owner walks, one for each merged feature; the ones that belong to one record of the demo
    # (a sales order, a finished order, a run, a lot, a machine) open the demo's own (STEPS_OF_A_RECORD) and are dropped where the demo has none. The words
    # fit any factory: the furniture workshop has its own list below.
    "manufacturing": (
        ("اعمل أمر إنتاج من طلب بيع", "Make a production order from a sales order", "الطلب مؤكد: بند اتفتح له أمر إنتاج وبند لسه مستني", "A confirmed order: one line has its production order, the other is still waiting", "manufacturing:tour_sales_order"),
        ("احتياج الخامات وأمر الشراء", "Material needs and the purchase order", "الناقص من الخامات، وأمر شراء مسودة له بضغطة", "What is short, and a draft purchase order for it in one tap", "manufacturing:mrp"),
        ("ابدأ مرحلة وسجّلها من «شغلي»", "Start a stage and record it on My work", "مرحلة شغالة ومرحلة مستنية: اضغط ابدأ، وسجّل السليم والمعيب لما تخلص", "A stage running and one waiting: press start, then record the good and the defective", "manufacturing:floor"),
        ("صرف إضافي وعمالة فعلية", "Extra issue and actual labour", "كارت التكلفة بيقارن الفعلي بالمعياري: خامة زيادة، خامة رجعت، وأجر العمل", "The cost card sets actual against standard: an extra material, one returned, and the labour", "manufacturing:order"),
        ("التشغيلة وصلاحيتها وملصقاتها", "The lot, its expiry and labels", "تشغيلة طالعة من أمر الإنتاج بصلاحيتها، ومنتج ثانوي خرج معاها", "A lot that came out of a production order with its expiry, and a by-product made with it", "manufacturing:run"),
        ("الإفراج من الجودة", "Quality release", "تشغيلة مستنية الفحص وتشغيلة اتفرج عنها بعد رفض جزء منها", "One lot waiting for its check, one released after part of it was rejected", "manufacturing:quality"),
        ("تتبّع التشغيلة", "Trace the lot", "من التشغيلة لخاماتها وفاتورة شرائها، ومنها للعملاء اللي اشتروا المنتج", "From the lot back to its materials and their purchase invoice, and forward to its customers", "manufacturing:trace"),
        ("جدول الإنتاج وماكينة واقفة", "The schedule and a machine that is down", "الأوامر على أيام الأسبوع، والمتأخر منها، وأي ماكينة واقفة دلوقتي", "Orders across the week, the late ones, and any machine that is down now", "manufacturing:schedule"),
        ("الصيانة وتكلفة الماكينة", "Maintenance and the machine's cost", "خطة صيانة مستحقة، وعطل اتصلّح بقطعة غيار وفاتورة فني", "A plan that is due, and a breakdown fixed with a spare part and a technician's bill", "manufacturing:maintenance_centre"),
        ("تقرير التكلفة والانحراف", "The cost and variance report", "الفرق بين الفعلي والمعياري لكل خامة، وأجر العمل المحمّل مقابل المدفوع", "Actual against standard for every material, and labour absorbed against labour paid", "manufacturing:costs"),
    ),
    # Contracting C13: twelve steps, one for each merged feature; the ones that belong to a project open the demo's own (STEPS_OF_A_RECORD).
    "contracting": (
        ("افتح المشروع ومقايسته", "Open the project and its bill of quantities", "بنود الأعمال بالكمية والفئة والإجمالي", "Work items by quantity, rate and total", "projects:boq"),
        ("المستخلص 3 بالكميات", "Progress bill 3 by quantities", "سجّل الكميات المنفذة، رحّله واطبعه للمالك", "Enter the work done, post it and print it for the owner", "projects:certificate"),
        ("أمر تغيير معتمد", "An approved variation order", "الأصلي مقابل المعدّل: كميات وبنود جديدة ومدة زيادة", "Original against revised: quantities, new items and extra time", "projects:variation"),
        ("شيك على المشروع", "A cheque on the project", "شيك تحت التحصيل وآخر اتحصّل؛ استلم شيك وارتجعه", "One cheque in hand and one cleared; receive one and bounce it", "projects:payments"),
        ("يومية الموقع بصورها", "The site diary with its photos", "الطقس والعمالة والمعدات والتأخير، وصور اليوم", "Weather, crew, equipment, delays and the day's photos", "projects:diary_day"),
        ("الجدول الزمني والبند المتأخر", "The schedule and its late task", "المخطط مقابل المنفذ وتنبيه التأخير", "Planned against actual, with the delay alert", "projects:schedule"),
        ("صرف خامات للموقع وارتجاع الفائض", "Issue materials to site and return the surplus", "إذن صرف بثلاثة أصناف وإذن ارتجاع بتكلفة المشروع", "A three-line issue voucher and a return at the project's cost", "projects:detail"),
        ("مستخلص مقاول الباطن بالكميات", "Bill the subcontractor by quantities", "دفعة مقدمة تُخصم من المستخلص، واحتجاز ضمان", "An advance recovered from the bill, and retention", "projects:subcontract"),
        ("خطابات الضمان", "Letters of guarantee", "خطاب ضمان قرب ينتهي، وتنبيه التجديد", "A letter close to its expiry, with the renewal alert", "guarantees:list"),
        ("طلبات الشراء من الموقع", "Purchase requests from the site", "طلب معتمد بأمر شراء مسودة، وطلب مستني القرار", "An approved request with its draft purchase order, and one waiting", "projects:purchase_requests"),
        ("دراسة العطاءات", "The tender study", "عطاء اترسّى علينا وعطاء خسرناه بسبب السعر", "A tender won and one lost on price", "tenders:list"),
        ("تقرير المشاريع", "The projects report", "المستخلصات والضمان والدفعة المقدمة", "Progress bills, retention and the advance", "projects:report"),
    ),
}

#: Video finding 20: a step whose screen only some sub-activities have. The dentist's tour offered «قائمة الانتظار»,
#: which answers 404 in a dentist (and in a lab, a vet and a physiotherapy centre): the queue is a clinic's and a
#: medical centre's screen. ``demo_first_run.tour_card`` drops a step whose predicate says this business has no such
#: screen, the same way it already drops one into a switched-off module or capability.
STEP_IS_THERE = {"medical:queue": "medical.queue.has_queue"}

#: Contracting C13, Restaurants close-out: a step whose screen belongs to one record of the demo (a project's bill of quantities, its newest
#: certificate; a table's order, the guest's menu of one table). ``reverse(url_name)`` cannot give its address, so the step's app names a
#: function that does (``STEP_PATHS``, by the name's namespace): it takes the url name and returns the path, or None when the demo has no
#: such record (an emptied demo), and ``demo_first_run.tour_card`` then drops the step. The path may carry its own query (the call
#: centre's caller, the check's coupon); the card adds its language to it.
STEP_PATHS = {"projects": "projects.demo.tour_path", "restaurant": "restaurant.demo.tour_path", "manufacturing": "manufacturing.demo.tour_path"}
STEPS_OF_A_RECORD = frozenset({
    "projects:boq", "projects:certificate", "projects:variation", "projects:payments", "projects:diary_day", "projects:schedule", "projects:detail",
    "projects:subcontract", "projects:purchase_requests",
    "restaurant:order_combo", "restaurant:order_split", "restaurant:check", "restaurant:table_menu", "restaurant:reservations", "restaurant:preorders",
    "restaurant:call", "restaurant:platforms", "restaurant:brands", "restaurant:dish",
    # Manufacturing F14: the sales order that made a production order, the finished order with its actuals (or the custom job), the run that made a
    # by-product, the released lot, a machine's maintenance page, a quotation line's specifications and its converted job.
    "manufacturing:tour_sales_order", "manufacturing:order", "manufacturing:run", "manufacturing:trace", "manufacturing:maintenance_centre",
    "manufacturing:quote_sheet", "manufacturing:tour_job_sheet",
})

# M8 (HG-088): the clinic's demo has the beds on, so its tour has a fourth step; the tour card drops it wherever beds are off.
# Video finding 20: a dentist has no reception queue, so the step above is dropped there; the day's report takes its
# place, and the card keeps its three steps.
DEMO_TOURS[("medical", "dental")] = DEMO_TOURS["medical"][:2] + (
    ("اقفل اليوم وشوف الربح", "Close the day and see the profit", "تقرير اليوم والخزنة في دقيقة", "The day's report and the cash in a minute", "reports:daily"),
)

DEMO_TOURS[("medical", "clinic")] = DEMO_TOURS["medical"] + (
    ("الأسرّة والإقامة", "Beds and stays", "مين على أنهي سرير، وفاتورة الإقامة مسودة عند الخروج", "Who is in which bed, and the stay's draft bill at discharge", "medical:beds"),
)


# Manufacturing F14: the furniture workshop makes to measure: a quotation with its specifications and cost sheet, the job it becomes, the saw's breakdown and sawdust.
DEMO_TOURS[("manufacturing", "furniture")] = (
    ("عرض سعر بمواصفات وبيان تكلفة", "A quotation with specifications and a cost sheet", "المقاسات والخشب والتشطيب تحت البند، وسعره من الخامات والعمالة والهامش", "Size, wood and finish under the line, priced from materials, labour and margin", "manufacturing:quote_sheet"),
    ("حوّله لأمر إنتاج خاص", "Make it a custom production order", "العميل وافق: للشغل ده صنف ووصفة وأمر إنتاج بتكلفته هو", "The customer accepted: the job gets its own item, recipe and production order", "manufacturing:tour_job_sheet"),
    ("تكلفة الأمر الخاص", "The custom order's cost", "الخامات والعمالة المخططة مقابل اللي اتصرف فعلًا لحد ما الأمر يخلص", "Planned materials and labour against what was really spent until the order finished", "manufacturing:order"),
    ("احتياج الخامات وأمر الشراء", "Material needs and the purchase order", "الناقص من الخامات، وأمر شراء مسودة له بضغطة", "What is short, and a draft purchase order for it in one tap", "manufacturing:mrp"),
    ("ابدأ مرحلة وسجّلها من «شغلي»", "Start a stage and record it on My work", "اضغط ابدأ على المرحلة، وسجّل السليم والمعيب لما تخلص", "Press start on the stage, then record the good and the defective when it is done", "manufacturing:floor"),
    ("جدول الإنتاج والمنشار الواقف", "The schedule and the saw that is down", "الأوامر على أيام الأسبوع، والمتأخر منها، والمنشار واقف دلوقتي", "Orders across the week, the late ones, and the saw is down now", "manufacturing:schedule"),
    ("صيانة المنشار وتكلفتها", "The saw's maintenance and cost", "خطة شحذ مستحقة، وعطل الشفرة بتكلفة قطعة الغيار", "A sharpening plan that is due, and the blade breakdown with the cost of its part", "manufacturing:maintenance_centre"),
    ("نشارة الخشب منتج ثانوي", "Sawdust as a by-product", "تشغيلة الكراسي بتطلّع نشارة بقيمتها، وبتدخل المخزن لوحدها", "The chairs' run also makes sawdust, with its value, and puts it in stock on its own", "manufacturing:run"),
    ("تقرير التكلفة والانحراف", "The cost and variance report", "الفرق بين الفعلي والمعياري لكل خامة، وأجر العمل المحمّل مقابل المدفوع", "Actual against standard for every material, and labour absorbed against labour paid", "manufacturing:costs"),
)


def demo_tour(activity, sub_activity):
    return DEMO_TOURS.get((activity, sub_activity)) or DEMO_TOURS.get(activity) or DEMO_TOURS["commercial"]


# TRACK-ACTS W02: the sample shop is called what it is ("صيدلية" for a pharmacy, not "محل").
DEMO_NAME = "حِسبة للتجارة (تجريبي)"
DEMO_NAMES = {
    ("commercial", "grocery"): "سوبر ماركت حِسبة التجريبي", ("commercial", "pharmacy"): "صيدلية حِسبة التجريبية",
    ("commercial", "wholesale"): "حِسبة لتجارة الجملة (تجريبي)", ("commercial", "online"): "متجر حِسبة أونلاين (تجريبي)",
    ("commercial", "fashion"): "حِسبة للملابس والأحذية (تجريبي)", ("commercial", "electronics"): "حِسبة للموبايلات والإلكترونيات (تجريبي)",
    ("restaurants", "cafe"): "كافيه حِسبة التجريبي", ("restaurants", "bakery"): "مخبز حِسبة التجريبي",
    ("medical", "dental"): "عيادة حِسبة للأسنان (تجريبي)", ("medical", "lab"): "معمل حِسبة للتحاليل (تجريبي)",
    ("medical", "medical_center"): "مركز حِسبة الطبي (تجريبي)", ("medical", "physio"): "مركز حِسبة للعلاج الطبيعي (تجريبي)",
    ("medical", "vet"): "عيادة حِسبة البيطرية (تجريبي)", ("education", "nursery"): "حضانة حِسبة (تجريبي)",
    ("services", "beauty"): "صالون حِسبة التجريبي",
    "restaurants": "مطعم حِسبة التجريبي", "medical": "عيادة حِسبة التجريبية", "education": "مركز حِسبة التعليمي (تجريبي)",
    "services": "حِسبة للخدمات (تجريبي)", "manufacturing": "مصنع حِسبة التجريبي", "contracting": "حِسبة للمقاولات (تجريبي)",
}


def demo_name(activity, sub_activity):
    return DEMO_NAMES.get((activity, sub_activity)) or DEMO_NAMES.get(activity) or DEMO_NAME


def _name_the_demo_shop():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    if profile and profile.display_name == DEMO_NAME:  # never a name the visitor typed
        profile.display_name = demo_name(profile.activity_slug, profile.sub_activity_slug)
        profile.save(update_fields=["display_name", "updated_at"])


def demo_activity_choices(lang):
    from settings_core import setup_catalog as catalog

    return [(f"{a}:{s}", f"{catalog.activity_label(a, lang)} · {catalog.sub_activity_label(a, s, lang)}") for a, s in DEMO_ACTIVITIES]


def switch_activity(request):
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect

    from permissions.services import user_has_permission
    from settings_core.models import ClientProfile

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    if request.method != "POST":
        return redirect("dashboard_snapshot")
    if not user_has_permission(request.user, "settings.view_settings"):
        raise PermissionDenied("Owner only.")
    activity, _, sub = (request.POST.get("activity") or "").partition(":")
    if (activity, sub) in DEMO_ACTIVITIES:
        # NAV-ACT: switch the modules and capabilities too, exactly as the setup
        # wizard would for this activity, so the menu shows what it would use.
        from settings_core import activity_fit
        from settings_core import capabilities as caps
        from settings_core.demo_ai import CAPABILITIES as AI_CAPABILITIES
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        # DEMO-FIT: the sub-activity's own suggestions too (a salon starts with appointments on).
        # AI demo (HG-078 dev. 7): the showcase keeps its AI features (on the mock, labelled as samples) on every activity,
        # each only where the activity already has the sections it needs (a clinic gets no purchases for the invoice photo).
        modules = activity_fit.default_modules(activity, sub)
        ai = tuple(slug for slug in AI_CAPABILITIES if set(caps.needs(slug)) <= set(modules))
        from .demo_templates import manufacturing_demo_extras, restaurant_demo_extras, restaurant_demo_modules, services_demo_extras

        extras = services_demo_extras(activity, sub) + restaurant_demo_extras(activity, sub) + manufacturing_demo_extras(activity, sub)
        chosen = tuple(dict.fromkeys(caps.default_selection(activity, sub) + ai + tuple(slug for slug in extras if caps.is_available(slug))))
        complete_setup(profile, activity, sub, ",".join(restaurant_demo_modules(activity, sub, modules)), user=request.user, capabilities_raw=",".join(chosen))
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    return redirect(f"/dashboard/?lang={lang}")


def restart(request):
    """POST from the login page or the demo bar: start the showcase over."""

    from django.contrib.auth import login
    from django.http import Http404
    from django.shortcuts import redirect

    from .demo_restart import restart_demo

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    if request.method != "POST":
        return redirect(f"/login/?lang={lang}")
    if getattr(request, "demo_fresh_copy", False):
        # DEMO-FAST: the visitor is already on a copy of the emptied demo.
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.get(username="owner")
    else:
        owner = restart_demo()
    login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
    return redirect(f"/setup/activity/?lang={lang}")


def sample(request):
    """After choosing the activity on a restarted demo: a month of sample trade."""

    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from django.shortcuts import redirect

    from permissions.services import user_has_permission

    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")
    lang = "en" if request.POST.get("lang") == "en" else "ar"
    if request.method != "POST":
        return redirect(f"/dashboard/?lang={lang}")
    if not user_has_permission(request.user, "settings.view_settings"):
        raise PermissionDenied("Owner only.")
    from .demo_templates import seed_sample_business

    # FEEDBACK-R1: a second press (double click, or back and press again) must
    # not try to add the same month twice; seed_sample_business adds it once.
    seed_sample_business()
    return redirect(f"/dashboard/?lang={lang}")
