# 117C Services Sub-Activity Selection Plan

Status: PLANNING_APPROVED
Approved by: Ahmed

Route:

```text
/setup/activity/services/
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
```

Planning rule:

```text
Service activity does not mean no inventory.
A service business may still use items, inventory, spare parts, consumables, purchases, and suppliers.
Detailed modules are selected later in the modules step.
```

Arabic cards:

```text
خدمات عامة
صيانة وإصلاح
عيادة / مركز طبي
صالون / مركز تجميل
مركز تعليمي / كورسات
مكتب مهني
تسويق وتصميم وخدمات رقمية
نشاط خدمي آخر
```

English cards:

```text
General services
Maintenance & Repair
Clinic / Medical center
Salon / Beauty center
Education / Courses Center
Professional Office
Marketing, Design & Digital Services
Other Service Activity
```

Slugs:

```text
general
maintenance
clinic
beauty
education
professional
digital_marketing
other
```

Functional rule:

```text
Next starts disabled.
Selecting a card enables Next.
Back returns to /setup/activity/ with current lang.
Next goes to /setup/modules/?lang=<lang>&activity=services&sub_activity=<slug>.
```

Visual rule:

```text
Reuse 117A shell only.
No new background.
No shell redesign.
Inner content only.
```
