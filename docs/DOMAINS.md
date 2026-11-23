# الدومينات: نُظم تك وحِسبة (DOMAINS-001)

قرار أحمد (٧ نوفمبر ٢٠٢٦): **nuzmtech.com** هو دومين الشركة الأم «نُظم تك»، وحِسبة منتج تحته.
كل منتج جديد بعدين ياخد نفس الشكل: `<المنتج>.nuzmtech.com`، فمافيش منتج بيزاحم التاني.

## التقسيمة

| العنوان | الاستخدام | الحالة |
|---|---|---|
| `nuzmtech.com` | موقع الشركة الأم (صفحة بسيطة، تتوسّع بعدين) | لسه؛ الدومين على Cloudflare ومستخدم للإيميل بس |
| `hesba.nuzmtech.com` | صفحة منتج حِسبة ومنها رابط «جرّب» | لسه |
| `demo.hesba.nuzmtech.com` | الديمو (`docs/DEMO_ON_RENDER.md`) | بدل `hesba-demo.onrender.com` |
| `<العميل>.hesba.nuzmtech.com` | نسخة كل عميل (`render.yaml` + `docs/GO_LIVE.md`) | بدل `hesba-<العميل>.onrender.com` |

روابط onrender القديمة تفضل شغّالة كاحتياطي؛ الرابط الرسمي اللي يتبعت للناس هو اللي تحت نُظم تك.

## ربط نسخة (ديمو أو عميل) بالدومين — خطوات بالإيد، مش من الكود

1. **Cloudflare → DNS** (zone `nuzmtech.com`): سجل `CNAME` باسم `demo.hesba` (أو `<العميل>.hesba`)
   يشاور على عنوان الخدمة في Render (مثلًا `hesba-demo.onrender.com`). Proxy: مطفي (DNS only)
   لحد ما Render يصدر الشهادة، وبعدها ممكن يتفتح.
2. **Render → الخدمة → Settings → Custom Domains:** ضيف نفس الاسم واستنى الشهادة.
3. **Render → Environment:** عدّل ثلاث متغيرات (الخدمة بتعيد التشغيل لوحدها):

   | المتغير | القيمة |
   |---|---|
   | `ALLOWED_HOSTS` | `demo.hesba.nuzmtech.com,hesba-demo.onrender.com` |
   | `CSRF_TRUSTED_ORIGINS` | `https://demo.hesba.nuzmtech.com,https://hesba-demo.onrender.com` |
   | `PUBLIC_BASE_URL` | `https://demo.hesba.nuzmtech.com` |

   `PUBLIC_BASE_URL` هو اللي بيتبني عليه أي رابط يطلع برّه البرنامج: الإيميل اليومي، روابط مشاركة
   الفواتير والكشوف، وبعدين الحجز الأونلاين ورابط العميل في مسار خدمي. لازم يكون على الدومين الرسمي.

مافيش تعديل في الكود لأي خطوة من دول؛ الدومين كله من متغيرات البيئة (`config/settings.py`).

## ملاحظات

- `SECURE_HSTS_INCLUDE_SUBDOMAINS` شغّال افتراضيًا في الإنتاج. ده مناسب لـ `hesba.nuzmtech.com` ونسخ
  العملاء تحته، بس **ماتشغّلش حِسبة على الدومين الأم نفسه** (`nuzmtech.com`) عشان ماتفرضش HTTPS على كل
  منتجات الشركة بالغلط.
- الإيميل على `nuzmtech.com` ماشي بـ Cloudflare Email Routing (استقبال بس). إرسال الملخص اليومي من
  عنوان `@nuzmtech.com` محتاج مزوّد SMTP بسجلات SPF/DKIM بتاعته؛ شوف `docs/DAILY_EMAIL_SETUP.md`.
- أي قرار جديد عن الدومينات يتسجّل هنا، وفي `docs/MAESTRO_LOG.md` تحت «تعليمات أحمد».
