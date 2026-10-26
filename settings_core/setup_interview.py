"""SETUP-INTERVIEW (ST6): the wizard asks about the business in its own words, not about modules and features.

Ahmed, 20 Oct: «اتتبع مسار كل زرار من البداية خالص ورتّب اللي بيظهر بعده حسب كل نشاط … مش شرط كل
الأنشطة يظهرلها نفس الكلام بنفس الصياغة حتى لو بيأدّوا نفس المهمة». The old steps 3 and 4 asked a shop
owner to choose among fifteen "modules" and twelve "features", in the same words for a clinic and a café.

Here every activity has its own questions, asked in its own words ("بتصرف أدوية من عندك؟" for a clinic,
"المكونات تتخصم مع كل طبق؟" for a restaurant). Each answer says what it gives, and a follow-up only
shows after the answer that makes it relevant (expiry dates after "I keep stock"). Behind each answer
sit the modules and features it switches on; complete_setup still adds what those need.

A question is left out when it has nothing left to decide for this business: its modules are already
on whatever the answers (required, or needed by a required one) and its features do not fit the
sub-activity (capabilities.fits). Defaults come from the sub-activity's presets, so accepting every
suggestion sets the business up exactly as the old steps did.
"""

from . import activity_fit
from . import capabilities as caps
from . import setup_catalog as catalog

#: key -> (modules, features, parent). A follow-up only counts, and only shows, while its parent is yes.
LIBRARY = {
    "till": ((), ("pos",), None),
    "shift": ((), ("shift_required",), "till"),
    "barcode": ((), ("barcode",), None),
    "credit": (("customers",), (), None),
    "instal": ((), ("installments",), None),
    "prices": ((), ("price_lists",), None),
    "quotes": ((), ("quotations",), None),
    "suppliers": (("suppliers", "purchases"), (), None),
    "stock": (("inventory",), (), None),
    "expiry": ((), ("batches_expiry",), "stock"),
    "serials": ((), ("serials",), "stock"),
    "drugs": ((), ("drugs",), "stock"),
    "variants": ((), ("variants",), None),
    "units": ((), ("units",), None),
    "booking": (("appointments_visits",), (), None),
    "team": (("employees_technicians",), (), None),
    "making": (("manufacturing",), (), None),
    "projects": (("projects",), (), None),
    "expenses": (("expenses",), (), None),
    "print": (("pdf_printing",), (), None),
    "vat": ((), ("vat",), None),
    "einv": ((), ("e_invoice",), "vat"),
    "assets": ((), ("fixed_assets",), None),
    # MARKET-PARITY: the features that came after the interview was written (Ahmed, 26 Oct: «تعدل الاسئله الي في البدايه»).
    "promos": ((), ("promotions",), None),
    "loyalty": ((), ("loyalty",), None),
    "store": ((), ("online_store",), None),
    "shipping": ((), ("shipping",), None),
    "ereceipt": ((), ("e_receipt",), "vat"),
}

#: The wording every activity starts from: (question, what it gives), each (ar, en). {customers} and the
#: like are the activity's own names for its modules (activity_fit.module_text).
BASE = {
    "till": (("بتبيع للزبون اللي واقف قدامك على الكاشير؟", "Do you sell to customers standing at a till?"),
             ("شاشة كاشير سريعة بالباركود، وحساب الباقي، وإيصال 80mm.", "A fast till screen with barcodes, change and an 80mm receipt.")),
    "shift": (("عايز كل كاشير يفتح وردية ويقفلها بحساب درجه؟", "Should each cashier open and close a shift, counting their drawer?"),
              ("مفيش بيع من غير وردية مفتوحة، وكل وردية بتتقفل بعدّ الفلوس اللي في الدرج.", "No sale without an open shift; each shift closes with a count of its drawer.")),
    "barcode": (("أصنافك عليها باركود، أو عايز تطبعلها باركود؟", "Do your items carry barcodes, or should Hesba print them?"),
                ("البيع بالماسح أو بكاميرا الموبايل، وطباعة ملصقات باركود بعد كل فاتورة شراء.", "Sell with a scanner or the phone camera, and print barcode labels after each purchase.")),
    "credit": (("بتبيع آجل وعايز تتابع حساب كل زبون؟", "Do you sell on credit and want each customer's account?"),
               ("قايمة {customers} وكشف حساب لكل واحد، واللي عليه فلوس بيظهر في لوحة القيادة.", "A list of {customers} with a statement each; who owes shows on the dashboard.")),
    "instal": (("بتبيع بالتقسيط؟", "Do you sell in instalments?"),
               ("جدول أقساط لكل فاتورة، ومواعيد الاستحقاق، وتنبيه بالقسط اللي عدّى ميعاده.", "An instalment plan per invoice, due dates, and a reminder when one is late.")),
    "quotes": (("بتبعت عروض أسعار للعملاء قبل ما يشتروا؟", "Do you send customers quotations before they buy?"),
               ("عرض سعر تطبعه أو تبعته واتساب، ولما العميل يوافق يبقى أمر بيع تفوتره مرة واحدة أو على دفعات.",
                "A quotation you print or send on WhatsApp; once accepted it becomes a sales order you invoice in one go or in parts.")),
    "prices": (("عندك أكتر من سعر للصنف (قطاعي وجملة، أو سعر خاص لعميل)؟", "Do items have more than one price (retail and wholesale, or a customer's own)?"),
               ("قوايم أسعار، والفاتورة بتحط سعر العميل لوحدها.", "Price lists; the invoice picks the customer's price by itself.")),
    "suppliers": (("بتشتري بضاعتك من موردين وعايز تعرف عليك كام لكل واحد؟", "Do you buy from suppliers and want to know what you owe each one?"),
                  ("فواتير شراء بتدخّل البضاعة بتكلفتها، وحساب لكل مورد.", "Purchase invoices that bring stock in at cost, and an account per supplier.")),
    "stock": (("عايز تعرف رصيد كل صنف عندك أول بأول؟", "Do you want to know how much of each item you have, as it changes?"),
              ("{inventory}: الرصيد بيتحدّث مع كل بيع وشراء، وتنبيه لما صنف يقرّب يخلص.", "{inventory}: the balance moves with every sale and purchase, with a warning when an item runs low.")),
    "drugs": (("عايز تدوّر على الدوا بالمادة الفعالة وتشوف بدايله اللي عندك، وتسجّل الروشتات؟", "Do you want to find drugs by active ingredient, see the alternatives you stock, and record prescriptions?"),
              ("قاعدة أدوية بالمادة الفعالة والتركيز، والبدائل اللي في المخزون، والروشتة بتتصرف بفاتورة بيع، والمزمنة بتتجدد في ميعادها.",
               "A drug database by active ingredient and strength, the alternatives in stock, prescriptions dispensed on a sales invoice, and chronic refills on time.")),
    "expiry": (("بضاعتك ليها تاريخ صلاحية؟", "Do your goods have expiry dates?"),
               ("تاريخ صلاحية لكل كمية داخلة، والبيع بيبدأ بالأقرب، وتنبيه قبل ما حاجة تخلص صلاحيتها.", "An expiry date for each batch in, sales take the nearest first, and a warning before anything expires.")),
    "serials": (("القطع اللي بتبيعها ليها سيريال أو IMEI وضمان؟", "Do the pieces you sell have a serial or IMEI and a warranty?"),
                ("كل قطعة برقمها من الشراء للبيع، وفترة الضمان على الفاتورة.", "Each piece followed by its number from purchase to sale, with its warranty on the invoice.")),
    "variants": (("بتبيع نفس الصنف بمقاسات أو ألوان؟", "Do you sell the same item in sizes or colours?"),
                 ("صنف واحد بمقاساته وألوانه، وكل مقاس ليه رصيده وباركوده.", "One item with its sizes and colours, each with its own stock and barcode.")),
    "units": (("بتشتري بوحدة وتبيع بوحدة تانية (كرتونة وقطعة مثلًا)؟", "Do you buy in one unit and sell in another (a carton and a piece, say)?"),
              ("وحدات للصنف، والتحويل بينها بيتحسب لوحده في البيع والشراء والمخزون.", "Units per item, converted by themselves in sales, purchases and stock.")),
    "booking": (("بتشتغل بحجوزات ومواعيد؟", "Do you work by bookings and appointments?"),
                ("جدول مواعيد، والحجز بيتحوّل لفاتورة لما الزبون ييجي.", "A diary of appointments; a booking becomes an invoice when the customer comes.")),
    "team": (("فيه ناس شغّالة معاك عايز تتابع شغلهم؟", "Do people work with you whose work you want to follow?"),
             ("{employees}، وكل عملية بتتسجّل باسم اللي عملها.", "{employees}, with each operation recorded under who did it.")),
    "making": (("بتصنّع منتجاتك بنفسك من خامات بوصفات؟", "Do you make your products yourself from materials, by recipe?"),
               ("وصفات لكل منتج، وتشغيلات بتصرف الخامات وتدخّل المنتج بتكلفته.", "A recipe per product, and runs that use the materials and bring the product in at cost.")),
    "projects": (("بتشتغل بمشاريع أو طلبيات كبيرة ليها عميل وميزانية؟", "Do you work in projects or large orders with a client and a budget?"),
                 ("كل مشروع بخاماته ومصاريفه ومستخلصاته، وربحه لوحده.", "Each project with its materials, costs and progress bills, and its own profit.")),
    "expenses": (("بتسجّل مصاريف الشغل (إيجار، كهربا، مرتبات)؟", "Do you record running costs (rent, power, wages)?"),
                 ("المصروفات بأنواعها، وبتطلع في صافي الربح.", "Expenses by type, counted in the net profit.")),
    "print": (("بتطبع الفاتورة أو تبعتها للزبون PDF على الواتساب؟", "Do you print invoices or send them as a PDF on WhatsApp?"),
              ("فاتورة A4 وإيصال 80mm وكشوف حساب جاهزة للطباعة والمشاركة.", "An A4 invoice, an 80mm receipt and statements ready to print or share.")),
    "vat": (("مسجّل في ضريبة القيمة المضافة؟", "Are you registered for VAT?"),
            ("نسبة ضريبة لكل صنف، تتحسب لوحدها في الفاتورة، وتقرير ضريبة المبيعات والمشتريات.", "A VAT rate per item, worked out on each invoice, and the sales and purchases tax report.")),
    "einv": (("ومطلوب منك الفاتورة الإلكترونية (مصلحة الضرائب)؟", "And are you required to issue e-invoices (Tax Authority)?"),
             ("بيانات كل فاتورة بصيغة المنظومة، وفحص الناقص قبل ما تبعت.", "Each invoice in the portal's format, checked for missing data before it is sent.")),
    "assets": (("عندك عربية أو مكن أو أجهزة عايز تحسب إهلاكها؟", "Do you have vehicles, machines or equipment to depreciate?"),
               ("سجل للأصول، وإهلاك شهري بيدخل في صافي الربح.", "A register of assets, with monthly depreciation in the net profit.")),
    "promos": (("بتعمل عروض وخصومات أو كوبونات للزباين؟", "Do you run offers, discounts or coupons?"),
               ("خصم % على قسم أو صنف، اشتري كذا وخد كذا، خصم لما الفاتورة تعدي مبلغ، وكوبونات بكود. أحسن عرض بيتطبق لوحده في الكاشير والفاتورة.",
                "% off a section or an item, buy X get Y, an amount off over a total, and coupons with a code. The best offer applies by itself at the till and on the invoice.")),
    "loyalty": (("عايز زباينك يجمعوا نقاط على مشترياتهم ويصرفوها خصم؟", "Should customers collect points on what they buy and spend them as a discount?"),
                ("كل فاتورة لعميل باسمه بتكسبه نقاط يصرفها في الكاشير أو الفاتورة، والإلغاء والمرتجع بيرجّعوها.",
                 "Each invoice to a named customer earns points, spent at the till or on an invoice; a cancellation or a return takes them back.")),
    "store": (("عندك متجر أونلاين (Shopify أو WooCommerce)؟", "Do you have an online store (Shopify or WooCommerce)?"),
              ("ملف بأصنافك وأسعارك ورصيدك ترفعه على المتجر، وطلبات المتجر بتدخل هنا فواتير مسودة تراجعها وترحّلها.",
               "A file of your items, prices and stock to upload to the store, and the store's orders come in here as draft invoices to check and post.")),
    "shipping": (("بتبعت طلبات مع شركة شحن والمندوب بيحصّل من الزبون؟", "Do you send orders with a courier who collects the money from the customer?"),
                 ("كل شحنة بحالتها، واللي حصّلته شركة الشحن بيبان مستحق عليها مش في خزنتك، لحد ما تسلّمه بعد مصاريفها.",
                  "Each parcel and where it stands; what the courier collected shows as owed by it, not as cash, until it hands it over less its fees.")),
    "ereceipt": (("ومطلوب منك الإيصال الإلكتروني لمبيعات الكاشير للمستهلك؟", "And must your till sales to consumers carry e-receipts?"),
                 ("كل بيعة كاشير بيطلعلها إيصال بصيغة المنظومة وعليه QR، وبيستنى في الطابور لحد ما تربط الجهاز على بوابة الإيصالات.",
                  "Every till sale gets a receipt in the portal's format with a QR, queued until the device is linked on the receipt portal.")),
}

#: Each activity's questions, in the order a person in that trade would think of them, and the words that
#: are its own. Keys missing from an activity are simply not asked there.
ACTIVITY_QUESTIONS = {
    catalog.COMMERCIAL: (
        ("till", {}), ("shift", {}), ("barcode", {}),
        ("stock", {}), ("expiry", {}), ("drugs", {}), ("serials", {"ask": ("الأجهزة اللي بتبيعها ليها سيريال أو IMEI وضمان؟", "Do the devices you sell have a serial or IMEI and a warranty?")}),
        ("variants", {"ask": ("بتبيع نفس الموديل بمقاسات وألوان؟", "Do you sell the same model in sizes and colours?")}),
        ("units", {"ask": ("بتشتري بالكرتونة وتبيع بالقطعة أو بالعلبة؟", "Do you buy by the carton and sell by the piece or the box?")}),
        ("suppliers", {}), ("credit", {}), ("quotes", {}), ("instal", {}), ("prices", {}),
        ("promos", {}), ("loyalty", {}), ("store", {}), ("shipping", {}),
        ("expenses", {"ask": ("بتسجّل مصاريف المحل (إيجار، كهربا، مرتبات)؟", "Do you record the shop's running costs (rent, power, wages)?")}),
        ("team", {"ask": ("فيه بيّاعين أو كاشيرين شغّالين معاك؟", "Do sales staff or cashiers work with you?"),
                  "gives": ("{employees} ومرتباتهم كل شهر، والحضور والإجازات، وكل عملية بتتسجّل باسم اللي عملها.",
                            "{employees} and their monthly payroll, attendance and leave, with each operation recorded under who did it.")}),
        ("print", {}), ("vat", {}), ("einv", {}), ("ereceipt", {}), ("assets", {}),
    ),
    catalog.RESTAURANTS: (
        ("till", {"ask": ("عندك كاشير بيحاسب الطلبات؟", "Does a cashier settle the orders?"),
                  "gives": ("شاشة كاشير بأزرار الأصناف، والدفع كاش أو فيزا، وإيصال 80mm.", "A till with item buttons, cash or card, and an 80mm receipt.")}),
        ("shift", {}),
        ("stock", {"ask": ("عايز المكونات تتخصم من المخزن مع كل طبق، وتعرف الطبق بيتكلف كام؟", "Should ingredients come off stock with every dish, so you know what a dish costs?"),
                   "gives": ("مكونات لكل طبق، وتكلفته وهامشه، والمخزن بيتخصم منه مع كل طلب.", "Ingredients per dish, its cost and margin, with stock taken off at each order.")}),
        ("units", {"ask": ("بتشتري الخامات بالكيلو أو الكرتونة وبتصرفها بالجرام أو القطعة؟", "Do you buy by the kilo or carton and use by the gram or piece?")}),
        ("expiry", {"ask": ("خاماتك ليها تاريخ صلاحية عايز تتابعه؟", "Do your ingredients have expiry dates to follow?")}),
        ("suppliers", {"ask": ("بتشتري الخامات من موردين وعايز تعرف عليك كام لكل واحد؟", "Do you buy ingredients from suppliers and want to know what you owe each?")}),
        ("team", {"ask": ("عندك جرسونات وطاقم مطبخ عايز تعرف مين عمل إيه؟", "Do waiters and kitchen staff work with you, and you want to know who did what?")}),
        ("making", {"ask": ("بتخبز أو بتصنّع منتجاتك بوصفات وكميات (عجين، حلويات)؟", "Do you bake or make your products by recipe in batches (dough, sweets)?")}),
        ("variants", {"ask": ("مشروباتك أو أصنافك بأحجام (صغير، وسط، كبير)؟", "Do your drinks or dishes come in sizes (small, medium, large)?")}),
        ("barcode", {"ask": ("بتبيع منتجات معبّأة عليها باركود؟", "Do you sell packaged products with barcodes?")}),
        ("credit", {"ask": ("عندك زباين أو شركات بتحاسبهم آخر الشهر؟", "Do some customers or companies settle at the end of the month?")}),
        ("promos", {"ask": ("بتعمل عروض على الأطباق والمشروبات أو كوبونات خصم؟", "Do you run offers on dishes and drinks, or discount coupons?")}),
        ("loyalty", {"ask": ("عايز زباينك الدايمين يجمعوا نقاط على كل طلب؟", "Should regular customers collect points on every order?")}),
        ("expenses", {"ask": ("بتسجّل مصاريف المطعم (إيجار، غاز، كهربا، مرتبات)؟", "Do you record running costs (rent, gas, power, wages)?")}),
        ("print", {}), ("vat", {}), ("einv", {}),
        ("ereceipt", {"ask": ("ومطلوب منك الإيصال الإلكتروني لطلبات الكاشير؟", "And must the till's orders carry e-receipts?")}),
        ("assets", {"ask": ("عندك أفران أو ثلاجات أو عربية دليفري عايز تحسب إهلاكها؟", "Do you have ovens, fridges or a delivery van to depreciate?")}),
    ),
    catalog.MEDICAL: (
        ("booking", {"ask": ("بتشتغل بحجز مواعيد للكشف؟", "Do patients book appointments?"),
                     "gives": ("جدول الحجوزات، وقايمة انتظار، والحجز بيتحوّل لكشف وفاتورة.", "The booking diary and a waiting list; a booking becomes a visit and an invoice.")}),
        ("team", {"ask": ("فيه أكتر من دكتور، أو طاقم تمريض واستقبال؟", "Is there more than one doctor, or nursing and reception staff?")}),
        ("stock", {"ask": ("بتصرف أدوية أو مستلزمات من عندك وعايز تعرف رصيدها؟", "Do you dispense medicines or supplies and want to know their balance?"),
                   "gives": ("{inventory}: المستلزمات بتتخصم مع كل كشف، وتنبيه لما حاجة تقرّب تخلص.", "{inventory}: supplies come off with each visit, with a warning when one runs low.")}),
        ("expiry", {"ask": ("الأدوية والمستلزمات ليها تاريخ صلاحية عايز تتابعه؟", "Do medicines and supplies have expiry dates to follow?")}),
        ("drugs", {"ask": ("عندك صيدلية داخلية بتصرف روشتات وعايز بدائل الدوا اللي عندك؟", "Do you run an in-house pharmacy that dispenses prescriptions and needs drug alternatives?")}),
        ("suppliers", {"ask": ("بتشتري المستلزمات من موردين أو شركات أدوية؟", "Do you buy supplies from suppliers or drug companies?")}),
        ("units", {"ask": ("بتشتري بالعلبة وتصرف بالقطعة أو الأمبول؟", "Do you buy by the box and use by the piece or ampoule?")}),
        ("till", {"ask": ("عندك كاشير على الاستقبال بيبيع منتجات أو مستلزمات؟", "Does reception sell products or supplies at a till?")}),
        ("shift", {}),
        ("barcode", {"ask": ("المنتجات اللي بتبيعها عليها باركود؟", "Do the products you sell carry barcodes?")}),
        ("instal", {"ask": ("بتقسّط خطط العلاج على المرضى؟", "Do patients pay treatment plans in instalments?")}),
        ("prices", {"ask": ("عندك أسعار مختلفة لنفس الخدمة (كشف عادي ومستعجل، أو تعاقدات)؟", "Does one service have several prices (regular and urgent, or contracts)?")}),
        ("expenses", {"ask": ("بتسجّل مصاريف العيادة (إيجار، مرتبات، صيانة أجهزة)؟", "Do you record running costs (rent, wages, equipment upkeep)?")}),
        ("print", {"ask": ("بتطبع الفاتورة أو الروشتة أو تبعتها للمريض PDF؟", "Do you print or send invoices and reports to patients as a PDF?")}),
        ("vat", {}), ("einv", {}),
        ("ereceipt", {"ask": ("ومطلوب منك الإيصال الإلكتروني لمبيعات الاستقبال للمرضى؟", "And must reception's sales to patients carry e-receipts?")}),
        ("assets", {"ask": ("عندك أجهزة طبية عايز تحسب إهلاكها؟", "Do you have medical equipment to depreciate?")}),
    ),
    catalog.EDUCATION: (
        ("booking", {"ask": ("بتنظّم الحصص بمواعيد ومجموعات؟", "Do you run classes by timetable and groups?"),
                     "gives": ("جدول الحصص والمجموعات، والحضور والغياب من شاشة واحدة.", "The class timetable and groups, with attendance on one screen.")}),
        ("team", {"ask": ("عندك مدرّسين بتحاسبهم بالحصة أو بالنسبة؟", "Do teachers work with you, paid per class or by share?")}),
        ("instal", {"ask": ("بتقسّط المصاريف على أولياء الأمور؟", "Do parents pay fees in instalments?")}),
        ("prices", {"ask": ("عندك أسعار مختلفة لنفس الكورس (إخوات، منح، مجموعات)؟", "Does one course have several prices (siblings, grants, groups)?")}),
        ("stock", {"ask": ("بتبيع كتب أو مذكرات وعايز تعرف رصيدها؟", "Do you sell books or notes and want to know their stock?")}),
        ("suppliers", {"ask": ("بتشتري الكتب والأدوات من موردين أو مطابع؟", "Do you buy books and supplies from suppliers or printers?")}),
        ("expenses", {"ask": ("بتسجّل مصاريف السنتر (إيجار، كهربا، مرتبات)؟", "Do you record running costs (rent, power, wages)?")}),
        ("print", {"ask": ("بتطبع إيصالات المصاريف أو تبعتها لولي الأمر PDF؟", "Do you print fee receipts or send them to parents as a PDF?")}),
        ("vat", {}), ("einv", {}), ("assets", {}),
    ),
    catalog.SERVICES: (
        ("booking", {}),
        ("team", {"ask": ("عندك فنيين أو موظفين بتوزّع عليهم الشغل؟", "Do technicians or staff work with you, sharing out the jobs?")}),
        ("stock", {"ask": ("بتستخدم قطع غيار أو خامات أو منتجات من عندك وعايز تعرف رصيدها؟", "Do you use spare parts, materials or products and want to know their stock?")}),
        ("serials", {"ask": ("الأجهزة اللي بتصلحها أو بتركّبها ليها سيريال وضمان؟", "Do the devices you repair or install have a serial and a warranty?")}),
        ("suppliers", {"ask": ("بتشتري القطع أو الخامات من موردين؟", "Do you buy parts or materials from suppliers?")}),
        ("units", {}),
        ("till", {"ask": ("عندك كاشير للدفع السريع على الاستقبال؟", "Is there a till at reception for quick payments?")}),
        ("shift", {}),
        ("barcode", {"ask": ("بتبيع منتجات عليها باركود؟", "Do you sell products with barcodes?")}),
        ("quotes", {"ask": ("بتبعت عروض أسعار للعملاء قبل ما تبدأ الشغل؟", "Do you send customers a quotation before starting the work?")}),
        ("instal", {}), ("prices", {"ask": ("عندك أكتر من سعر لنفس الخدمة (عادي ومستعجل، أو عقود)؟", "Does one service have several prices (regular and urgent, or contracts)?")}),
        ("promos", {"ask": ("بتعمل عروض على الخدمات أو كوبونات خصم؟", "Do you run offers on your services, or discount coupons?")}),
        ("loyalty", {"ask": ("عايز زباينك الدايمين يجمعوا نقاط على كل زيارة؟", "Should regular customers collect points on every visit?")}),
        ("expenses", {}), ("print", {}), ("vat", {}), ("einv", {}), ("ereceipt", {}), ("assets", {}),
    ),
    catalog.MANUFACTURING: (
        ("credit", {"ask": ("بتبيع لموزعين أو تجار آجل وعايز تتابع حساباتهم؟", "Do you sell to distributors or traders on credit and want their accounts?")}),
        ("quotes", {"ask": ("بتسعّر الطلبية للعميل قبل ما تصنّعها (عرض سعر)؟", "Do you price an order for the customer before making it (a quotation)?"),
                    "gives": ("عرض سعر تطبعه أو تبعته واتساب، ولما العميل يوافق يبقى أمر بيع تفوتره مع كل دفعة بتسلّمها.",
                              "A quotation you print or send on WhatsApp; once accepted it becomes a sales order invoiced with each delivery.")}),
        ("prices", {"ask": ("عندك سعر للموزع غير سعر القطاعي؟", "Do distributors get a different price from retail?")}),
        ("units", {"ask": ("بتشتري الخامات بالكيلو أو الطن وتبيع بالقطعة أو الكرتونة؟", "Do you buy materials by the kilo or tonne and sell by the piece or carton?")}),
        ("expiry", {"ask": ("خاماتك أو منتجاتك ليها تاريخ صلاحية؟", "Do your materials or products have expiry dates?")}),
        ("variants", {"ask": ("منتجاتك بمقاسات وألوان؟", "Do your products come in sizes and colours?")}),
        ("serials", {"ask": ("كل منتج ليه رقم مسلسل وضمان؟", "Does each product have a serial number and a warranty?")}),
        ("barcode", {"ask": ("منتجاتك عليها باركود، أو عايز تطبعلها باركود؟", "Do your products carry barcodes, or should Hesba print them?")}),
        ("team", {"ask": ("عندك عمال وفنيين على الإنتاج؟", "Do workers and technicians run production?")}),
        ("projects", {"ask": ("بتشتغل بطلبيات كبيرة لعملاء كأنها مشاريع؟", "Do you work on large customer orders as projects?")}),
        ("till", {"ask": ("عندك منفذ بيع بكاشير؟", "Do you have a shop with a till?")}),
        ("shift", {}), ("instal", {}),
        ("store", {"ask": ("بتبيع منتجاتك على متجر أونلاين (Shopify أو WooCommerce)؟", "Do you sell your products in an online store (Shopify or WooCommerce)?")}),
        ("shipping", {"ask": ("بتبعت الطلبيات مع شركة شحن بتحصّل من العميل؟", "Do you send orders with a courier who collects from the customer?")}),
        ("expenses", {"ask": ("بتسجّل مصاريف المصنع (إيجار، كهربا، مرتبات)؟", "Do you record running costs (rent, power, wages)?")}),
        ("print", {}), ("vat", {}), ("einv", {}), ("ereceipt", {}),
        ("assets", {"ask": ("عندك مكن ومعدات عايز تحسب إهلاكها؟", "Do you have machines and equipment to depreciate?")}),
    ),
    catalog.CONTRACTING: (
        ("suppliers", {"ask": ("بتشتري مواد وبتشتغل مع مقاولين باطن وعايز تتابع حساباتهم؟", "Do you buy materials and work with subcontractors whose accounts you follow?"),
                       "gives": ("فواتير شراء المواد على كل مشروع، وحساب لكل مورد ومقاول باطن.", "Material purchases charged to each project, and an account per supplier and subcontractor.")}),
        ("stock", {"ask": ("بتخزّن مواد في مخزن الشركة أو الموقع؟", "Do you keep materials in a company or site store?")}),
        ("units", {"ask": ("المواد بتتحسب بالمتر والطن والشيكارة؟", "Are materials counted in metres, tonnes and bags?")}),
        ("team", {"ask": ("عندك مهندسين ومشرفين وعمال بتتابع شغلهم؟", "Do engineers, supervisors and workers work with you?")}),
        ("booking", {"ask": ("عندك زيارات صيانة أو معاينات بمواعيد؟", "Do you have maintenance visits or site surveys by appointment?")}),
        ("expenses", {"ask": ("بتسجّل مصاريف الشركة والمواقع (إيجار، نقل، مرتبات)؟", "Do you record company and site costs (rent, transport, wages)?")}),
        ("print", {"ask": ("بتطبع المستخلصات والفواتير أو تبعتها PDF؟", "Do you print progress bills and invoices or send them as a PDF?")}),
        ("vat", {}), ("einv", {}),
        ("assets", {"ask": ("عندك معدات (لودر، خلاطة، سقالات) عايز تحسب إهلاكها؟", "Do you have equipment (loaders, mixers, scaffolding) to depreciate?")}),
    ),
}
#: Whose questions a business is asked when they are not its own activity's: a clinic or a tutoring centre
#: listed under services works like the medical or education trade, and "other" like what it said it is closest to.
ASKED_AS = {(catalog.SERVICES, "clinic"): catalog.MEDICAL, (catalog.SERVICES, "education"): catalog.EDUCATION,
            (catalog.OTHER, "products"): catalog.COMMERCIAL}


def asked_as(activity, sub_activity):
    if (activity, sub_activity) in ASKED_AS:
        return ASKED_AS[(activity, sub_activity)]
    return catalog.SERVICES if activity == catalog.OTHER else activity


def _text(pair, lang):
    return pair[1 if lang == "en" else 0]


def _names(activity, sub_activity, lang):
    return {key: activity_fit.module_text(activity, sub_activity, slug, lang)[0]
            for key, slug in (("customers", "customers"), ("suppliers", "suppliers"), ("inventory", "inventory"),
                              ("items", "items_services"), ("employees", "employees_technicians"))}


def forced_modules(activity, sub_activity):
    """The modules this business always gets: the required ones and what they need."""

    from .setup_services import with_needs

    offered = activity_fit.module_rows(activity, sub_activity, "ar")
    return set(with_needs([row["slug"] for row in offered if row["preset"] == catalog.REQUIRED]))


def _plan(activity, sub_activity):
    """{key: (modules, features, parent, default)} for the questions this business is asked."""

    asked_activity = asked_as(activity, sub_activity)
    near_activity, near_sub = catalog.closest(activity, sub_activity)
    offered = {row["slug"]: row["preset"] for row in activity_fit.module_rows(activity, sub_activity, "ar")}
    forced = forced_modules(activity, sub_activity)
    plan = {}
    for key, _ in ACTIVITY_QUESTIONS.get(asked_activity, ()):
        modules, features, parent = LIBRARY[key]
        modules = tuple(slug for slug in modules if slug in offered and slug not in forced)
        features = tuple(slug for slug in features if caps.is_available(slug) and caps.fits(near_activity, slug, near_sub))
        if not modules and not features:
            continue  # nothing left to decide here
        if parent and parent not in plan:
            parent = None  # its parent is not asked (already settled for this business), so it stands on its own
        default = any(offered.get(slug) == catalog.SUGGESTED for slug in modules) or any(
            caps.preset_state(near_activity, near_sub, slug) == caps.SUGGESTED for slug in features)
        plan[key] = (modules, features, parent, default)
    # A suggested follow-up suggests the answer it hangs on (a polyclinic keeps expiry dates, so it keeps stock).
    for key, (modules, features, parent, default) in list(plan.items()):
        if default and parent and not plan[parent][3]:
            plan[parent] = plan[parent][:3] + (True,)
    return plan


def questions(activity, sub_activity, lang="ar"):
    """The questions in order: [{key, ask, gives, parent, default, modules, features}]."""

    overrides = dict(ACTIVITY_QUESTIONS.get(asked_as(activity, sub_activity), ()))
    names = _names(activity, sub_activity, lang)
    plan = _plan(activity, sub_activity)
    # the activity's own screens come with the module that holds them (Codex on #258)
    screens = {}
    for screen in activity_fit.features(activity, sub_activity, lang):
        screens.setdefault(screen["module"], []).append(screen["label"])
    # each follow-up straight under the answer it hangs on
    order = []
    for key, (_, _, parent, _) in plan.items():
        if not parent:
            order.append(key)
            order += [child for child, entry in plan.items() if entry[2] == key]
    rows = []
    for key in order:
        modules, features, parent, default = plan[key]
        ask, gives = BASE[key]
        ask, gives = overrides[key].get("ask", ask), overrides[key].get("gives", gives)
        rows.append({"key": key, "ask": _text(ask, lang), "gives": _text(gives, lang).format(**names), "parent": parent or "",
                     "default": default, "modules": ",".join(modules), "features": ",".join(features),
                     "labels": [activity_fit.module_text(activity, sub_activity, slug, lang)[0] for slug in modules]
                     + [caps.label(slug, lang) for slug in features]
                     + [label for slug in modules for label in screens.get(slug, ())]})
    return rows


def resolve(activity, sub_activity, yes):
    """(modules, features) for the answers: the required modules, plus what each yes switches on.

    A follow-up counts only while its parent is yes; complete_setup adds what the chosen modules
    and features need."""

    yes = set(yes)
    plan = _plan(activity, sub_activity)
    offered = {row["slug"]: row["preset"] for row in activity_fit.module_rows(activity, sub_activity, "ar")}
    modules = [slug for slug, preset in offered.items() if preset == catalog.REQUIRED]
    features = []
    for key, (key_modules, key_features, parent, _) in plan.items():
        if key not in yes or (parent and parent not in yes):
            continue
        modules += [slug for slug in key_modules if slug not in modules]
        features += [slug for slug in key_features if slug not in features]
    return modules, features


def suggested_answers(activity, sub_activity):
    """The yes answers that set the business up as its presets would."""

    return [key for key, (_, _, _, default) in _plan(activity, sub_activity).items() if default]
