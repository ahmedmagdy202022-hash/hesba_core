# النسخ الاحتياطي الليلي على Google Drive بتاع العميل (BACKUP-003)

## الفكرة في سطرين
- كل ليلة حسبة بتعمل نسخة احتياطية **مشفّرة بمفتاح العميل** (BACKUP-002) وبترفعها على **Google Drive بتاع العميل نفسه**، في فولدر اسمه «Hesba Backups».
- حسبة بتطلب صلاحية `drive.file` بس: تشوف وتعدّل **الملفات اللي هي عملتها** (فولدر النسخ)، ومش بتشوف أي حاجة تانية في الـ Drive.
- مفيش حاجة بتترفع من غير تشفير. لو المفتاح مش معمول، الرفع بيرفض.

## مرة واحدة لتطبيق حسبة كله (أحمد)
ده إعداد لتطبيق حسبة نفسه، مش لكل عميل. ببلاش، ومش محتاج فيزا.

1. <https://console.cloud.google.com> ← مشروع جديد باسم «Hesba».
2. **APIs & Services ← Library**: فعّل **Google Drive API**.
3. **OAuth consent screen**:
   - User type: **External**.
   - اسم التطبيق «حِسبة – Hesba»، وإيميل الدعم بتاعك.
   - Scopes: ضيف `.../auth/drive.file` **بس**.
   - **Publishing status: In production** ← مهم جدًا. لو فضل «Testing»، الربط بيقع لوحده بعد 7 أيام.
   - `drive.file` صلاحية قليلة المخاطر، وعادةً مش محتاجة مراجعة كاملة من جوجل. ممكن تظهر للعميل رسالة «التطبيق ده لسه ما اتراجعش» لحد ما تعمل Brand verification، وده طبيعي.
4. **Credentials ← Create credentials ← OAuth client ID ← Web application**:
   - Authorized redirect URI لكل موقع عميل:
     `https://<موقع-العميل>/settings/backups/drive/callback/`
5. خد **Client ID** و**Client secret** وحطهم في متغيرات البيئة:

| المتغير | القيمة |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | الـ Client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | الـ Client secret |
| `NIGHTLY_TOKEN` | نص عشوائي طويل (مثلاً ناتج `python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
| `BACKUP_DRIVE_KEEP` | عدد النسخ اللي تفضل في الـ Drive (الافتراضي 30) |

## اللي بيعمله العميل (دقيقتين)
1. **الإعدادات ← النسخ الاحتياطي ← إنشاء مفتاح**، وبعدها يحفظ المفتاح في مكانين.
2. **ربط Google Drive**: يختار حسابه ويوافق.
3. يدوس **«اعمل نسخة وارفعها دلوقتي»** ويتأكد إن الملف ظهر في فولدر «Hesba Backups» في الـ Drive بتاعه.

## التشغيل كل ليلة
- **لو السيرفر عنده cron**: `python manage.py nightly_backup` مرة في اليوم.
- **لو مفيش cron** (زي خدمة ويب مجانية): أي منبّه خارجي مجاني (زي cron-job.org) يبعت مرة في اليوم:
  ```
  POST https://<موقع-العميل>/ops/nightly/
  X-Hesba-Token: <NIGHTLY_TOKEN>
  ```
  - بيرجّع `{"status": "uploaded"}` أو `"skipped"` (لو نسخة النهارده اترفعت خلاص) أو `"failed"`.
  - من غير التوكن الصح بيرجّع 404، ومش بيرجّع أي داتا.
- آخر رفع وآخر مشكلة بيظهروا في صفحة النسخ الاحتياطي، وكل رفع أو فشل متسجل في سجل المراجعة.

## الاسترجاع
على أي جهاز عليه حسبة:
```
python manage.py decrypt_backup hesba-YYYYMMDD-HHMMSS.hesba-backup --key-file مفتاحي.txt
python manage.py migrate
python manage.py loaddata hesba-YYYYMMDD-HHMMSS.json.gz
```

## مين يقدر يعمل إيه
| | العميل | أحمد / السيرفر | جوجل |
|---|---|---|---|
| يشوف ملفات الـ Drive التانية | ✅ | ❌ (`drive.file` بس) | — |
| يفتح النسخة المشفّرة | ✅ بمفتاحه | ❌ | ❌ |
| يوقف الرفع | ✅ «فصل Google Drive» أو من إعدادات حسابه في جوجل | — | — |
