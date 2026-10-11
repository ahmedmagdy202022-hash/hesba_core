"""R2-5: sample data that looks like the business that chose it.

``seed_demo_business`` always builds the same shape of business: five items,
four customers, two suppliers, the same invoices on the same days, and the
same three planted problems (item 4 under its minimum, item 5 sold out,
customer 3 past their credit limit). Only what those rows are *called* and
what they cost changes with the activity: a pharmacy sells medicines by the
strip, a builder buys cement by the bag, a clinic sells visits.

Item positions keep their roles in every catalog:

* items 1-3 sell every day. They may be services (``stock=False``): a visit,
  a haircut, a dish. Services are never bought.
* item 4 is the one that runs low and item 5 the one that sells out, so both
  are always stocked goods.

``raw`` lists supplies bought once into stock (ingredients, spare parts,
materials). With ``recipes`` a catalog makes its goods instead of buying them:
what a purchase would have brought in is produced from those supplies, through
the manufacturing services.
"""

from dataclasses import dataclass, field
from decimal import Decimal


D = Decimal


@dataclass(frozen=True)
class Product:
    name: str
    unit: str
    sale: Decimal
    purchase: Decimal
    stock: bool = True


@dataclass(frozen=True)
class Supply:
    code: str
    name: str
    unit: str
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class Catalog:
    key: str
    category: tuple
    items: tuple
    customers: tuple
    suppliers: tuple
    raw: tuple = ()
    #: item index -> (output quantity per batch, ((raw index, quantity per batch), ...))
    recipes: dict = field(default_factory=dict)
    #: (name, title) of the technician, doctor, teacher... appointments are booked with.
    staff: tuple = ("أحمد الفني", "فني")
    #: what this business also shows: tables, appointments, patients, projects, batches, orders
    extras: frozenset = frozenset()


def _p(name, unit, sale, purchase="0", stock=True):
    return Product(name, unit, D(sale), D(purchase), stock)


def _s(name, unit, sale):
    return Product(name, unit, D(sale), D("0"), False)


def _raw(code, name, unit, price, quantity):
    return Supply(code, name, unit, D(price), D(quantity))


PEOPLE = ("أحمد عبد الله", "سارة محمود", "محمد إبراهيم")

#: The shop every demo had before R2-5; still the one used without a profile.
BASE = Catalog(
    key="base",
    category=("ملابس وأحذية", "Clothing"),
    items=(
        _p("قميص قطن", "قطعة", "240.00", "150.00"),
        _p("بنطلون جينز", "قطعة", "380.00", "250.00"),
        _p("حزام جلد", "قطعة", "120.00", "70.00"),
        _p("حقيبة ظهر", "قطعة", "450.00", "300.00"),
        _p("جراب موبايل", "قطعة", "90.00", "45.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مصنع الدلتا للملابس", "موردون متحدون"),
)

RETAIL = Catalog(
    key="commercial.retail",
    category=("أدوات منزلية", "Household goods"),
    items=(
        _p("طقم كوبايات زجاج (6)", "طقم", "180.00", "115.00"),
        _p("حلة ستانلس 24 سم", "قطعة", "650.00", "450.00"),
        _p("مناديل مطبخ (6 لفات)", "عبوة", "75.00", "52.00"),
        _p("كشاف LED شحن", "قطعة", "220.00", "150.00"),
        _p("شنطة تسوق قماش", "قطعة", "45.00", "25.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("شركة الأمل للأدوات المنزلية", "موزعون متحدون"),
)

GROCERY = Catalog(
    key="commercial.grocery",
    category=("مواد غذائية", "Groceries"),
    items=(
        _p("أرز مصري 1 كجم", "كيس", "38.00", "31.00"),
        _p("زيت عباد الشمس 1 لتر", "زجاجة", "85.00", "72.00"),
        _p("سكر 1 كجم", "كيس", "36.00", "30.00"),
        _p("شاي ناعم 250 جم", "علبة", "60.00", "48.00"),
        _p("جبنة بيضاء 500 جم", "علبة", "70.00", "55.00"),
    ),
    customers=PEOPLE + ("مطعم أبو علي",),
    suppliers=("شركة المنصورة للمواد الغذائية", "مخازن الشروق للجملة"),
    extras=frozenset({"batches"}),
)

FASHION = Catalog(
    key="commercial.fashion",
    category=("ملابس وأحذية", "Clothing & shoes"),
    items=(
        _p("قميص قطن", "قطعة", "240.00", "150.00"),
        _p("بنطلون جينز", "قطعة", "380.00", "250.00"),
        _p("حزام جلد", "قطعة", "120.00", "70.00"),
        _p("حذاء رياضي", "جوز", "650.00", "420.00"),
        _p("شراب قطن (3 أزواج)", "عبوة", "90.00", "45.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مصنع الدلتا للملابس", "مصنع الهرم للأحذية"),
)

ELECTRONICS = Catalog(
    key="commercial.electronics",
    category=("موبايلات وإكسسوارات", "Mobiles & accessories"),
    items=(
        _p("سماعة بلوتوث", "قطعة", "450.00", "320.00"),
        _p("شاحن سريع 25 وات", "قطعة", "350.00", "230.00"),
        _p("كابل شحن Type-C", "قطعة", "120.00", "60.00"),
        _p("موبايل 128 جيجا", "جهاز", "8500.00", "7600.00"),
        _p("جراب موبايل", "قطعة", "90.00", "45.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مؤسسة النيل للموبايلات", "الشركة المصرية للإكسسوارات"),
    staff=("أحمد فني الصيانة", "فني صيانة موبايلات"),
)

PHARMACY = Catalog(
    key="commercial.pharmacy",
    category=("أدوية ومستلزمات", "Medicines & supplies"),
    items=(
        _p("باراسيتامول 500 مجم", "شريط", "35.00", "27.00"),
        _p("فيتامين سي 1000 فوار", "أنبوبة", "95.00", "72.00"),
        _p("شراب كحة للأطفال 100 مل", "زجاجة", "55.00", "42.00"),
        _p("أموكسيسيلين 500 مجم", "علبة", "85.00", "68.00"),
        _p("كمامات طبية (50)", "علبة", "60.00", "38.00"),
    ),
    customers=PEOPLE + ("مستشفى الرحمة الخاص",),
    suppliers=("الشركة المتحدة لتوزيع الأدوية", "مخزن الشفاء للأدوية"),
    staff=("د. منى الصيدلانية", "صيدلانية"),
    extras=frozenset({"batches"}),
)

WHOLESALE = Catalog(
    key="commercial.wholesale",
    category=("مواد غذائية جملة", "Wholesale groceries"),
    items=(
        _p("كرتونة مياه معدنية (12)", "كرتونة", "95.00", "80.00"),
        _p("كرتونة زيت (12 لتر)", "كرتونة", "960.00", "870.00"),
        _p("كرتونة مكرونة (20 كيس)", "كرتونة", "260.00", "225.00"),
        _p("شيكارة سكر 50 كجم", "شيكارة", "1700.00", "1560.00"),
        _p("كرتونة شاي (24 علبة)", "كرتونة", "1350.00", "1220.00"),
    ),
    customers=("سوبر ماركت الهدى", "بقالة الأمانة", "ماركت السلام", "شركة النور للتجارة"),
    suppliers=("مصنع الدلتا للمواد الغذائية", "شركة الوادي للتوزيع"),
)

ONLINE = Catalog(
    key="commercial.online",
    category=("منتجات أونلاين", "Online products"),
    items=(
        _p("ساعة ذكية", "قطعة", "1200.00", "850.00"),
        _p("سماعة بلوتوث", "قطعة", "450.00", "320.00"),
        _p("حامل موبايل للعربية", "قطعة", "150.00", "80.00"),
        _p("شنطة لابتوب", "قطعة", "550.00", "380.00"),
        _p("رينج لايت للتصوير", "قطعة", "400.00", "260.00"),
    ),
    customers=("أحمد عبد الله (أونلاين)", "سارة محمود (إنستجرام)", "محمد إبراهيم (واتساب)", "شركة النور للتجارة"),
    suppliers=("مستورد الصين المباشر", "شركة الشحن السريع"),
)

# --- services: items 1-3 are what the business sells its time for ---------

SERVICES_GENERAL = Catalog(
    key="services.general",
    category=("خدمات", "Services"),
    items=(
        _s("خدمة تركيب", "خدمة", "150.00"),
        _s("زيارة فنية", "زيارة", "250.00"),
        _s("عقد خدمة شهري", "شهر", "900.00"),
        _p("خامات تشغيل", "عبوة", "120.00", "80.00"),
        _p("قطع استهلاكية", "قطعة", "60.00", "35.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("شركة التوريدات الفنية", "موردون متحدون"),
    raw=(_raw("DEMO-RAW-01", "عدة وأدوات صغيرة", "طقم", "300.00", "4"),),
    extras=frozenset({"appointments"}),
)

MAINTENANCE = Catalog(
    key="services.maintenance",
    category=("صيانة أجهزة منزلية", "Appliance repair"),
    items=(
        _s("كشف وتشخيص", "زيارة", "150.00"),
        _s("مصنعية إصلاح", "خدمة", "300.00"),
        _p("فلتر تكييف", "قطعة", "120.00", "70.00"),
        _p("موتور غسالة", "قطعة", "1800.00", "1350.00"),
        _p("طرمبة غسالة", "قطعة", "350.00", "240.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مخزن قطع الغيار المركزي", "موردون متحدون"),
    raw=(_raw("DEMO-RAW-01", "فريون تكييف (أسطوانة)", "أسطوانة", "1400.00", "3"),
         _raw("DEMO-RAW-02", "مواسير نحاس (متر)", "متر", "180.00", "20")),
    staff=("أحمد الفني", "فني صيانة"),
    extras=frozenset({"appointments"}),
)

BEAUTY = Catalog(
    key="services.beauty",
    category=("خدمات ومنتجات تجميل", "Beauty services & products"),
    items=(
        _s("قص وتصفيف شعر", "خدمة", "250.00"),
        _s("صبغة شعر", "خدمة", "700.00"),
        _s("مانيكير وباديكير", "خدمة", "300.00"),
        _p("شامبو احترافي", "زجاجة", "280.00", "190.00"),
        _p("ماسك شعر", "علبة", "220.00", "140.00"),
    ),
    customers=("منى عبد الله", "سارة محمود", "ريهام إبراهيم", "شركة النور للتجارة (عروسة)"),
    suppliers=("شركة الجمال لمستحضرات التجميل", "موزع المنتجات الاحترافية"),
    raw=(_raw("DEMO-RAW-01", "صبغة احترافية (أنبوبة)", "أنبوبة", "120.00", "30"),),
    staff=("منى الكوافيرة", "كوافيرة"),
    extras=frozenset({"appointments"}),
)

PROFESSIONAL = Catalog(
    key="services.professional",
    category=("خدمات مهنية", "Professional services"),
    items=(
        _s("استشارة", "جلسة", "500.00"),
        _s("إعداد إقرار ضريبي", "خدمة", "1500.00"),
        _s("مراجعة حسابات شهرية", "شهر", "3000.00"),
        _p("دفاتر وسجلات", "دفتر", "80.00", "50.00"),
        _p("ملفات حفظ", "ملف", "25.00", "15.00"),
    ),
    customers=("شركة الأمل للتجارة", "مؤسسة النور", "م. محمد إبراهيم", "شركة النور للتجارة"),
    suppliers=("مكتبة المعرفة", "موردون متحدون"),
    staff=("أ. محمود المحاسب", "محاسب"),
    extras=frozenset({"appointments"}),
)

DIGITAL = Catalog(
    key="services.digital_marketing",
    category=("تسويق وتصميم", "Marketing & design"),
    items=(
        _s("تصميم بوست", "تصميم", "300.00"),
        _s("تصوير منتج", "جلسة", "800.00"),
        _s("إدارة صفحة شهريًا", "شهر", "4000.00"),
        _p("رول أب بانر", "قطعة", "450.00", "300.00"),
        _p("فلاير مطبوع (100)", "عبوة", "250.00", "150.00"),
    ),
    customers=("مطعم أبو علي", "صيدلية الشفاء", "محل الأناقة", "شركة النور للتجارة"),
    suppliers=("مطبعة الوادي", "موردون متحدون"),
    staff=("كريم المصمم", "مصمم"),
    extras=frozenset({"appointments"}),
)

# --- restaurants: dishes are made to order, drinks are bought and resold ----

def _kitchen(*ingredients):
    return tuple(_raw(f"DEMO-RAW-{n:02d}", name, unit, price, qty) for n, (name, unit, price, qty) in enumerate(ingredients, start=1))


RESTAURANT = Catalog(
    key="restaurants.restaurant",
    category=("المنيو", "Menu"),
    items=(
        _s("نص فرخة مشوية", "طبق", "180.00"),
        _s("كفتة مشوية (ربع)", "طبق", "220.00"),
        _s("مكرونة بشاميل", "طبق", "120.00"),
        _p("مياه معدنية صغيرة", "زجاجة", "15.00", "8.00"),
        _p("مشروب غازي كانز", "كانز", "25.00", "15.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة (غدا موظفين)",),
    suppliers=("مزارع الدلتا للدواجن", "شركة المشروبات المتحدة"),
    raw=_kitchen(("فراخ طازة", "كجم", "95.00", "40"), ("لحمة مفرومة", "كجم", "380.00", "15"), ("أرز", "كجم", "31.00", "25")),
    staff=("كريم الويتر", "ويتر"),
    extras=frozenset({"tables"}),
)

CAFE = Catalog(
    key="restaurants.cafe",
    category=("المشروبات", "Drinks"),
    items=(
        _s("قهوة تركي", "فنجان", "45.00"),
        _s("كابتشينو", "كوب", "70.00"),
        _s("شاي بالنعناع", "كوب", "30.00"),
        _p("كرواسون جاهز", "قطعة", "45.00", "28.00"),
        _p("مياه معدنية صغيرة", "زجاجة", "15.00", "8.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("محمصة البن الذهبي", "مخبز الصباح"),
    raw=_kitchen(("بن محوج", "كجم", "450.00", "6"), ("لبن كامل الدسم", "لتر", "38.00", "30"), ("سكر", "كجم", "30.00", "15")),
    staff=("كريم الباريستا", "باريستا"),
    extras=frozenset({"tables"}),
)

FAST_FOOD = Catalog(
    key="restaurants.fast_food",
    category=("الوجبات", "Meals"),
    items=(
        _s("ساندوتش برجر", "ساندوتش", "120.00"),
        _s("شاورما فراخ", "ساندوتش", "90.00"),
        _s("بطاطس كبيرة", "علبة", "50.00"),
        _p("مشروب غازي كانز", "كانز", "25.00", "15.00"),
        _p("مياه معدنية صغيرة", "زجاجة", "15.00", "8.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مصنع اللحوم المجمدة", "شركة المشروبات المتحدة"),
    raw=_kitchen(("برجر مجمد", "قطعة", "28.00", "120"), ("صدور فراخ", "كجم", "160.00", "15"), ("بطاطس مجمدة", "كجم", "45.00", "40")),
    staff=("كريم الكاشير", "كاشير"),
    extras=frozenset({"tables"}),
)

BAKERY = Catalog(
    key="restaurants.bakery",
    category=("حلويات ومخبوزات", "Bakery & sweets"),
    items=(
        _s("جاتوه (قطعة)", "قطعة", "45.00"),
        _s("تورتة شوكولاتة", "تورتة", "350.00"),
        _s("كيلو بسبوسة", "كجم", "160.00"),
        _p("شمع أعياد ميلاد", "علبة", "25.00", "12.00"),
        _p("علبة تورتة كرتون", "علبة", "15.00", "8.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مطاحن الدلتا", "شركة مستلزمات الحلواني"),
    raw=_kitchen(("دقيق فاخر", "كجم", "22.00", "50"), ("سمنة", "كجم", "95.00", "15"), ("شوكولاتة خام", "كجم", "320.00", "8")),
    staff=("أسطى حسن الحلواني", "حلواني"),
)

CLOUD_KITCHEN = Catalog(
    key="restaurants.cloud_kitchen",
    category=("وجبات دليفري", "Delivery meals"),
    items=(
        _s("وجبة فراخ كومبو", "وجبة", "210.00"),
        _s("طاجن لحمة", "طاجن", "260.00"),
        _s("سلطة سيزر", "طبق", "95.00"),
        _p("مشروب غازي كانز", "كانز", "25.00", "15.00"),
        _p("علبة تغليف", "علبة", "12.00", "6.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة",),
    suppliers=("مزارع الدلتا للدواجن", "شركة التغليف الحديثة"),
    raw=_kitchen(("فراخ طازة", "كجم", "95.00", "30"), ("لحمة بتلو", "كجم", "420.00", "10"), ("خضار سلطة", "كجم", "25.00", "20")),
    staff=("كريم الطيار", "طيار دليفري"),
)

# --- medical: visits and tests are sold; a few supplies are on the shelf ---

def _medical(key, category, services, goods, staff, suppliers=("الشركة المتحدة للمستلزمات الطبية", "مخزن الشفاء")):
    return Catalog(
        key=key, category=category, items=tuple(_s(*s) for s in services) + tuple(_p(*g) for g in goods),
        customers=PEOPLE + ("شركة النور للتجارة (تأمين موظفين)",), suppliers=suppliers, staff=staff,
        raw=(_raw("DEMO-RAW-01", "قفازات طبية (علبة)", "علبة", "90.00", "20"), _raw("DEMO-RAW-02", "سرنجات (علبة 100)", "علبة", "150.00", "10")),
        extras=frozenset({"appointments", "patients"}),
    )


CLINIC = _medical(
    "medical.clinic", ("كشوفات ومستلزمات", "Visits & supplies"),
    (("كشف", "كشف", "400.00"), ("استشارة (إعادة)", "كشف", "200.00"), ("رسم قلب", "خدمة", "250.00")),
    (("شرائط قياس سكر (25)", "علبة", "220.00", "160.00"), ("قناع جلسة بخار", "قطعة", "150.00", "95.00")),
    ("د. أحمد سالم", "طبيب باطنة"),
)

DENTAL = _medical(
    "medical.dental", ("علاج أسنان", "Dental care"),
    (("كشف أسنان", "كشف", "300.00"), ("حشو عصب", "جلسة", "1500.00"), ("تنظيف جير", "جلسة", "600.00")),
    (("فرشاة أسنان طبية", "قطعة", "60.00", "35.00"), ("معجون حساسية", "أنبوبة", "85.00", "60.00")),
    ("د. هالة فؤاد", "طبيبة أسنان"),
)

MEDICAL_CENTER = _medical(
    "medical.medical_center", ("كشوفات وخدمات", "Visits & services"),
    (("كشف باطنة", "كشف", "450.00"), ("كشف أطفال", "كشف", "400.00"), ("سونار", "خدمة", "600.00")),
    (("شرائط قياس سكر (25)", "علبة", "220.00", "160.00"), ("جهاز قياس ضغط", "جهاز", "950.00", "700.00")),
    ("د. أحمد سالم", "استشاري باطنة"),
)

LAB = _medical(
    "medical.lab", ("تحاليل وأشعة", "Tests & imaging"),
    (("صورة دم كاملة", "تحليل", "150.00"), ("وظائف كبد", "تحليل", "300.00"), ("أشعة عادية", "أشعة", "350.00")),
    (("كوب عينة معقم", "قطعة", "10.00", "5.00"), ("أنبوبة سحب دم", "قطعة", "15.00", "8.00")),
    ("د. سامي المعملي", "طبيب تحاليل"),
)

PHYSIO = _medical(
    "medical.physio", ("علاج طبيعي", "Physiotherapy"),
    (("جلسة علاج طبيعي", "جلسة", "350.00"), ("كشف وتقييم", "كشف", "400.00"), ("جلسة تمرينات", "جلسة", "250.00")),
    (("حزام دعم ظهر", "قطعة", "450.00", "300.00"), ("رباط ركبة طبي", "قطعة", "280.00", "180.00")),
    ("د. ياسمين حسن", "أخصائية علاج طبيعي"),
)

VET = Catalog(
    key="medical.vet",
    category=("خدمات بيطرية", "Veterinary"),
    items=(
        _s("كشف بيطري", "كشف", "250.00"),
        _s("تطعيم سنوي", "جرعة", "400.00"),
        _s("تنظيف وتجميل", "خدمة", "300.00"),
        _p("أكل قطط 2 كجم", "كيس", "380.00", "290.00"),
        _p("شامبو حيوانات", "زجاجة", "160.00", "100.00"),
    ),
    customers=PEOPLE + ("شركة النور للتجارة (كلاب حراسة)",),
    suppliers=("الشركة المصرية للأدوية البيطرية", "مخزن أغذية الحيوانات"),
    raw=(_raw("DEMO-RAW-01", "تطعيمات (علبة 10)", "علبة", "2200.00", "2"),),
    staff=("د. كريم البيطري", "طبيب بيطري"),
    extras=frozenset({"appointments"}),
)

# --- education: subscriptions are sold; books and kits are stocked ----------

def _education(key, category, services, goods, staff):
    return Catalog(
        key=key, category=category, items=tuple(_s(*s) for s in services) + tuple(_p(*g) for g in goods),
        customers=("أحمد عبد الله (ولي أمر)", "سارة محمود (ولية أمر)", "محمد إبراهيم", "شركة النور للتجارة (منح موظفين)"),
        suppliers=("مطبعة المعرفة", "مكتبة الوادي"), staff=staff, extras=frozenset({"appointments", "students"}),
    )


TUTORING = _education(
    "education.tutoring_center", ("حصص ومذكرات", "Classes & notes"),
    (("رياضيات - اشتراك شهر", "شهر", "400.00"), ("فيزياء - اشتراك شهر", "شهر", "400.00"), ("حصة مراجعة", "حصة", "150.00")),
    (("مذكرة رياضيات", "مذكرة", "120.00", "60.00"), ("مذكرة فيزياء", "مذكرة", "120.00", "60.00")),
    ("أ. محمود المدرس", "مدرس رياضيات"),
)

TRAINING = _education(
    "education.training", ("كورسات", "Courses"),
    (("كورس Excel", "كورس", "1500.00"), ("كورس محاسبة عملي", "كورس", "2500.00"), ("ورشة تسويق", "ورشة", "800.00")),
    (("كتاب الكورس", "كتاب", "250.00", "140.00"), ("شنطة المتدرب", "قطعة", "150.00", "90.00")),
    ("م. هاني المدرب", "مدرب"),
)

LANGUAGES = _education(
    "education.languages", ("كورسات لغات", "Language courses"),
    (("إنجليزي - مستوى 1", "مستوى", "1800.00"), ("ألماني A1", "مستوى", "2500.00"), ("اختبار تحديد مستوى", "اختبار", "200.00")),
    (("كتاب إنجليزي", "كتاب", "300.00", "180.00"), ("كتاب ألماني", "كتاب", "350.00", "220.00")),
    ("Ms. سارة", "مدرسة إنجليزي"),
)

NURSERY = _education(
    "education.nursery", ("اشتراكات الحضانة", "Nursery fees"),
    (("اشتراك شهري", "شهر", "1800.00"), ("يوم إضافي", "يوم", "120.00"), ("نشاط فني", "نشاط", "200.00")),
    (("زي الحضانة", "طقم", "350.00", "220.00"), ("شنطة أدوات", "قطعة", "250.00", "160.00")),
    ("مس هبة", "مشرفة"),
)

# --- contracting: materials bought, used on site and sold on ----------------

def _contracting(key, category, goods, extras=frozenset({"projects"}), services=()):
    return Catalog(
        key=key, category=category, items=tuple(_s(*s) for s in services) + tuple(_p(*g) for g in goods),
        customers=("شركة النور للتطوير العقاري", "م. أحمد عبد الله", "مدارس المستقبل الخاصة", "شركة الدلتا للاستثمار"),
        suppliers=("شركة الدلتا لمواد البناء", "مؤسسة الوادي للتوريدات"), staff=("م. سامح مهندس الموقع", "مهندس موقع"), extras=extras,
    )


CONTRACTING_GENERAL = _contracting(
    "contracting.general", ("مواد بناء", "Building materials"),
    (("أسمنت (شيكارة 50 كجم)", "شيكارة", "230.00", "200.00"), ("رمل (متر مكعب)", "م³", "350.00", "280.00"),
     ("طوب أحمر (ألف)", "ألف طوبة", "2200.00", "1900.00"), ("حديد تسليح", "طن", "39000.00", "36500.00"),
     ("سيراميك أرضيات", "م²", "280.00", "210.00")),
)

FINISHING = _contracting(
    "contracting.finishing", ("خامات تشطيب", "Finishing materials"),
    (("معجون حوائط (شيكارة)", "شيكارة", "180.00", "140.00"), ("دهان بلاستيك (9 لتر)", "جركن", "950.00", "780.00"),
     ("جبس بورد (لوح)", "لوح", "320.00", "250.00"), ("بورسلين 60×60", "م²", "450.00", "350.00"),
     ("سيراميك حوائط", "م²", "280.00", "210.00")),
)

MEP = _contracting(
    "contracting.electromechanical", ("خامات كهروميكانيك", "MEP materials"),
    (("مفتاح كهرباء", "قطعة", "45.00", "28.00"), ("ماسورة PVC 4 بوصة", "ماسورة", "220.00", "170.00"),
     ("خلاط حمام", "قطعة", "850.00", "600.00"), ("وحدة تكييف 1.5 حصان", "جهاز", "21000.00", "18500.00"),
     ("سلك كهرباء 2 مم (لفة)", "لفة", "1400.00", "1150.00")),
)

INFRASTRUCTURE = _contracting(
    "contracting.infrastructure", ("خامات طرق", "Road materials"),
    (("سن (متر مكعب)", "م³", "380.00", "300.00"), ("بلدورة (متر)", "متر", "160.00", "120.00"),
     ("أسمنت (شيكارة 50 كجم)", "شيكارة", "230.00", "200.00"), ("أسفلت", "طن", "3200.00", "2800.00"),
     ("ماسورة صرف 8 بوصة", "ماسورة", "900.00", "720.00")),
)

MAINTENANCE_CONTRACTS = _contracting(
    "contracting.maintenance_contracts", ("عقود صيانة", "Maintenance contracts"),
    (("فلتر تكييف مركزي", "قطعة", "650.00", "450.00"), ("كونتاكتور كهرباء", "قطعة", "380.00", "260.00")),
    services=(("عقد صيانة شهري", "شهر", "6000.00"), ("زيارة طوارئ", "زيارة", "1200.00"), ("صيانة دورية", "زيارة", "2500.00")),
    extras=frozenset({"projects", "appointments"}),
)

# --- manufacturing: goods are made from raw materials, not bought ----------

def _factory(key, category, goods, raw, recipes, staff, suppliers, customers):
    return Catalog(
        key=key, category=category, items=tuple(_p(*g) for g in goods), customers=customers, suppliers=suppliers,
        raw=tuple(_raw(f"DEMO-RAW-{n:02d}", *r) for n, r in enumerate(raw, start=1)), recipes=recipes, staff=staff,
        extras=frozenset({"orders"}),
    )


DISTRIBUTORS = ("سوبر ماركت الهدى", "محلات الأمانة", "محمد إبراهيم", "شركة النور للتجارة")

FOOD = _factory(
    "manufacturing.food", ("منتجات غذائية", "Food products"),
    (("بسكويت بالشوكولاتة (كرتونة)", "كرتونة", "420.00", "260.00"), ("كيك فانيليا (كرتونة)", "كرتونة", "380.00", "240.00"),
     ("معمول بالتمر (كرتونة)", "كرتونة", "300.00", "180.00"), ("تورتة جاهزة", "تورتة", "450.00", "280.00"),
     ("بسكويت سادة (كرتونة)", "كرتونة", "250.00", "150.00")),
    (("دقيق", "كجم", "22.00", "150"), ("سكر", "كجم", "30.00", "80"), ("زبدة", "كجم", "180.00", "30"), ("شوكولاتة خام", "كجم", "320.00", "15")),
    {0: (D("1"), ((0, D("4")), (1, D("2")), (3, D("0.3")))), 1: (D("1"), ((0, D("4")), (1, D("2")), (2, D("0.5")))),
     2: (D("1"), ((0, D("3")), (1, D("1")), (2, D("0.4")))), 3: (D("1"), ((0, D("2")), (1, D("1.5")), (2, D("0.5")), (3, D("0.3")))),
     4: (D("1"), ((0, D("4")), (1, D("2"))))},
    ("أسطى حسن مشرف الخط", "مشرف إنتاج"), ("مطاحن الدلتا", "شركة السكر والتكرير"), DISTRIBUTORS,
)

GARMENTS = _factory(
    "manufacturing.garments", ("ملابس جاهزة", "Ready garments"),
    (("تيشيرت قطن", "قطعة", "180.00", "95.00"), ("بنطلون جينز", "قطعة", "350.00", "210.00"), ("ترنج رياضي", "طقم", "420.00", "260.00"),
     ("قميص رجالي", "قطعة", "300.00", "170.00"), ("مفرش سرير", "طقم", "550.00", "330.00")),
    (("قماش قطن", "متر", "65.00", "300"), ("قماش جينز", "متر", "110.00", "120"), ("خيط", "بكرة", "15.00", "80"), ("زراير وسوست", "طقم", "8.00", "300")),
    {0: (D("1"), ((0, D("1.2")), (2, D("0.2")))), 1: (D("1"), ((1, D("1.5")), (2, D("0.3")), (3, D("1")))),
     2: (D("1"), ((0, D("2.5")), (2, D("0.4")), (3, D("1")))), 3: (D("1"), ((0, D("1.8")), (2, D("0.3")), (3, D("1")))),
     4: (D("1"), ((0, D("4.5")), (2, D("0.5"))))},
    ("أسطى سيد مشرف التفصيل", "مشرف إنتاج"), ("مصنع المحلة للغزل والنسيج", "شركة الإكسسوارات الحديثة"), DISTRIBUTORS,
)

FURNITURE = _factory(
    "manufacturing.furniture", ("أثاث", "Furniture"),
    (("كرسي خشب", "قطعة", "900.00", "520.00"), ("ترابيزة سفرة", "قطعة", "3500.00", "2100.00"), ("كومودينو", "قطعة", "1200.00", "700.00"),
     ("دولاب 3 ضلف", "قطعة", "7500.00", "4600.00"), ("رف حائط", "قطعة", "350.00", "190.00")),
    (("خشب زان (لوح)", "لوح", "850.00", "40"), ("MDF (لوح)", "لوح", "650.00", "30"), ("مسامير ومفصلات", "علبة", "60.00", "60"), ("ورنيش (لتر)", "لتر", "140.00", "40")),
    {0: (D("1"), ((0, D("0.4")), (2, D("0.3")), (3, D("0.3")))), 1: (D("1"), ((0, D("1.8")), (2, D("1")), (3, D("1.2")))),
     2: (D("1"), ((1, D("0.8")), (2, D("0.5")), (3, D("0.4")))), 3: (D("1"), ((1, D("5")), (2, D("2")), (3, D("2")))),
     4: (D("1"), ((1, D("0.2")), (2, D("0.2")), (3, D("0.2"))))},
    ("أسطى محمود النجار", "رئيس النجارين"), ("مخزن الأخشاب المركزي", "شركة الإكسسوارات الحديثة"),
    ("معرض الأمل للموبيليا", "محمد إبراهيم", "سارة محمود", "شركة النور للتطوير العقاري"),
)

PRINTING = _factory(
    "manufacturing.printing", ("مطبوعات وتغليف", "Print & packaging"),
    (("علبة كرتون مطبوعة (100)", "رزمة", "650.00", "380.00"), ("كتالوج ملون (100)", "رزمة", "1800.00", "1050.00"),
     ("كروت شخصية (1000)", "علبة", "300.00", "150.00"), ("شنطة ورق مطبوعة (100)", "رزمة", "900.00", "540.00"),
     ("ستيكر منتجات (1000)", "رول", "450.00", "250.00")),
    (("ورق كوشيه (رزمة)", "رزمة", "1400.00", "20"), ("كرتون مقوى (لوح)", "لوح", "18.00", "800"), ("حبر طباعة (كجم)", "كجم", "450.00", "15")),
    {0: (D("1"), ((1, D("15")), (2, D("0.2")))), 1: (D("1"), ((0, D("0.5")), (2, D("0.3")))), 2: (D("1"), ((0, D("0.08")), (2, D("0.05")))),
     3: (D("1"), ((0, D("0.2")), (1, D("5")), (2, D("0.1")))), 4: (D("1"), ((0, D("0.1")), (2, D("0.15"))))},
    ("أسطى عادل الطباع", "مشرف المطبعة"), ("شركة الورق الحديثة", "موزع الأحبار"), DISTRIBUTORS,
)

CHEMICALS = _factory(
    "manufacturing.chemicals", ("منظفات", "Detergents"),
    (("سائل أطباق 1 لتر (كرتونة 12)", "كرتونة", "420.00", "250.00"), ("مسحوق غسيل 1 كجم (كرتونة 10)", "كرتونة", "650.00", "400.00"),
     ("كلور 1 لتر (كرتونة 12)", "كرتونة", "240.00", "130.00"), ("ملمع زجاج (كرتونة 12)", "كرتونة", "380.00", "220.00"),
     ("صابون سائل (كرتونة 12)", "كرتونة", "450.00", "270.00")),
    (("مادة فعالة (سلفونيك)", "كجم", "95.00", "120"), ("صودا كاوية", "كجم", "45.00", "60"), ("عبوات بلاستيك فاضية", "عبوة", "4.00", "1500"), ("عطر صناعي", "لتر", "350.00", "10")),
    {0: (D("1"), ((0, D("1.5")), (2, D("12")), (3, D("0.05")))), 1: (D("1"), ((0, D("2.5")), (1, D("1")), (3, D("0.05")))),
     2: (D("1"), ((1, D("1.2")), (2, D("12")))), 3: (D("1"), ((0, D("1")), (2, D("12")), (3, D("0.05")))),
     4: (D("1"), ((0, D("1.8")), (2, D("12")), (3, D("0.08"))))},
    ("م. رامي الكيميائي", "مسؤول الإنتاج"), ("شركة الكيماويات المتحدة", "مصنع العبوات البلاستيك"), DISTRIBUTORS,
)

WORKSHOP = _factory(
    "manufacturing.workshop", ("منتجات الورشة", "Workshop products"),
    (("باب حديد", "قطعة", "4500.00", "2900.00"), ("شباك ألوميتال", "قطعة", "2200.00", "1400.00"), ("سور بلكونة (متر)", "متر", "900.00", "550.00"),
     ("بوابة جراج", "قطعة", "12000.00", "7800.00"), ("حامل تكييف", "قطعة", "350.00", "180.00")),
    (("حديد مشغول (عود)", "عود", "420.00", "80"), ("قطاع ألوميتال (متر)", "متر", "160.00", "120"), ("لحام (علبة)", "علبة", "180.00", "20"), ("دهان حديد (لتر)", "لتر", "150.00", "30")),
    {0: (D("1"), ((0, D("5")), (2, D("0.5")), (3, D("1.5")))), 1: (D("1"), ((1, D("7")), (2, D("0.2")))),
     2: (D("1"), ((0, D("1.5")), (2, D("0.2")), (3, D("0.5")))), 3: (D("1"), ((0, D("14")), (2, D("1.5")), (3, D("4")))),
     4: (D("1"), ((0, D("0.3")), (2, D("0.1")), (3, D("0.1"))))},
    ("أسطى رضا الحداد", "رئيس الورشة"), ("مخزن الحديد والصلب", "شركة الألوميتال الحديثة"),
    ("م. أحمد عبد الله", "سارة محمود", "محمد إبراهيم", "شركة النور للتطوير العقاري"),
)

CATALOGS = {
    "commercial": {"retail": RETAIL, "grocery": GROCERY, "fashion": FASHION, "electronics": ELECTRONICS, "pharmacy": PHARMACY,
                   "wholesale": WHOLESALE, "online": ONLINE, None: RETAIL},
    "services": {"general": SERVICES_GENERAL, "maintenance": MAINTENANCE, "clinic": CLINIC, "beauty": BEAUTY, "education": TRAINING,
                 "professional": PROFESSIONAL, "digital_marketing": DIGITAL, None: SERVICES_GENERAL},
    "restaurants": {"restaurant": RESTAURANT, "cafe": CAFE, "fast_food": FAST_FOOD, "bakery": BAKERY, "cloud_kitchen": CLOUD_KITCHEN,
                    None: RESTAURANT},
    "medical": {"clinic": CLINIC, "dental": DENTAL, "medical_center": MEDICAL_CENTER, "lab": LAB, "physio": PHYSIO, "vet": VET,
                None: CLINIC},
    "education": {"tutoring_center": TUTORING, "training": TRAINING, "languages": LANGUAGES, "nursery": NURSERY, "private_tutor": TUTORING,
                  None: TUTORING},
    "contracting": {"general": CONTRACTING_GENERAL, "finishing": FINISHING, "electromechanical": MEP, "infrastructure": INFRASTRUCTURE,
                    "maintenance_contracts": MAINTENANCE_CONTRACTS, None: CONTRACTING_GENERAL},
    "manufacturing": {"food": FOOD, "garments": GARMENTS, "furniture": FURNITURE, "printing": PRINTING, "chemicals": CHEMICALS,
                      "workshop": WORKSHOP, None: FOOD},
    "other": {"mixed": SERVICES_GENERAL, None: RETAIL},
}


def catalog_for(activity, sub_activity):
    """The catalog for an activity and sub-activity; an unknown one gets the activity's usual one."""

    by_sub = CATALOGS.get(activity)
    if by_sub is None:
        return BASE
    return by_sub.get(sub_activity) or by_sub[None]


def active_catalog():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    if profile is None or not profile.activity_slug:
        return BASE
    return catalog_for(profile.activity_slug, profile.sub_activity_slug)


def all_catalogs():
    seen, result = set(), [BASE]
    for by_sub in CATALOGS.values():
        for catalog in by_sub.values():
            if catalog.key not in seen and catalog is not BASE:
                seen.add(catalog.key)
                result.append(catalog)
    return result
