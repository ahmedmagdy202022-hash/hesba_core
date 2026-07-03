# 119 Review Setup Plan

Status: PLANNING_APPROVED
Approved by: Ahmed

Route:

```text
/setup/review/?lang=<lang>&activity=<activity>&sub_activity=<slug>&modules=<selected_modules>
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
docs/118_MODULES_SELECTION_PLAN.md
```

Visual rule:

```text
Reuse the approved 117A setup shell.
No new background.
No shell redesign.
Inner content only.
Stepper active step is 4: Review / المراجعة.
```

Purpose:

```text
Review setup choices before finishing the initial setup flow.
Show activity, sub-activity, and selected modules.
```

Arabic content:

```text
Title: راجع إعدادات نشاطك
Subtitle: تأكد من الاختيارات التالية قبل إنهاء إعداد حِسْبَة لنشاطك.
Important note: يمكنك تعديل الموديولات لاحقًا من الإعدادات، ولن يتم حذف أي بيانات عند تعطيل موديول.
Back: الرجوع إلى اختيار الموديولات
Next: إنهاء الإعداد
```

English content:

```text
Title: Review your setup
Subtitle: Confirm the following choices before finishing your Hesba setup.
Important note: You can adjust modules later from Settings. Disabling a module will not delete any existing data.
Back: Back to modules selection
Next: Finish setup
```

Behavior:

```text
Back returns to /setup/modules/ with current lang, activity, sub_activity, and modules.
Next goes to /setup/complete/?lang=<lang> as a safe placeholder.
No real database save.
No final production activation.
No irreversible setup decision.
```

Important product rule:

```text
Initial setup chooses the starting configuration only.
Users can later enable, disable, or adjust modules from Settings.
Disabling a module must never delete existing data.
Required modules may be locked or owner-only later.
Module dependency rules belong to a later Module Settings stage.
```

Future stage:

```text
120 Module Settings / إعدادات الموديولات
```

Implementation status:

```text
Planning approved only.
No implementation approved yet.
A Sub Chat implementation message is required before coding.
```
