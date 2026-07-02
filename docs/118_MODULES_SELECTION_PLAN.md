# 118 Modules Selection Plan

Status: PLANNING_APPROVED
Approved by: Ahmed

Route:

```text
/setup/modules/?lang=<lang>&activity=<activity>&sub_activity=<slug>
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
```

Visual rule:

```text
Reuse the approved 117A setup shell.
No new background.
No shell redesign.
Inner content only.
Stepper active step is 3: Modules / الموديولات.
```

Core decision:

```text
118 uses smart default modules.
Hesba suggests modules based on activity and sub_activity.
The user can edit suggested and optional modules before continuing.
```

Module states:

```text
Required  = ON and locked.
Suggested = ON by default and editable.
Optional  = OFF by default and editable.
```

Initial module list:

```text
customers
suppliers
items_services
sales_operations
purchases
inventory
cashboxes
expenses
reports
pdf_printing
appointments_visits
employees_technicians
```

Commercial preset:

```text
Required:
sales_operations, items_services, cashboxes, reports

Suggested:
customers, suppliers, purchases, inventory, expenses, pdf_printing

Optional:
appointments_visits, employees_technicians
```

Services preset:

```text
Required:
items_services, customers, cashboxes, reports

Suggested:
expenses, pdf_printing

Optional:
suppliers, purchases, inventory, appointments_visits, employees_technicians
```

Important business rule:

```text
Services can still use items, inventory, spare parts, consumables, purchases, and suppliers.
This depends on selected modules, not only on activity type.
```

Back behavior:

```text
activity=commercial -> /setup/activity/commercial/?lang=<lang>
activity=services   -> /setup/activity/services/?lang=<lang>
missing/unknown     -> /setup/activity/?lang=<lang>
```

Next behavior:

```text
Go to /setup/review/?lang=<lang>&activity=<activity>&sub_activity=<slug>&modules=<selected_modules>
```

Implementation status:

```text
Planning approved only.
No implementation approved yet.
A Screen Pack / Sub Chat message is required before coding.
```
