# 117C Services Sub-Activity Visual Approval

Status: VISUAL_APPROVED
Approved by: Ahmed

Branch:

```text
feature/117c-services-subactivity-selection
```

Approved route:

```text
/setup/activity/services/
```

Approved preview checks:

```text
/setup/activity/services/?lang=ar
/setup/activity/services/?lang=en
```

Source of truth:

```text
docs/117A_SETUP_FLOW_VISUAL_LOCK.md
docs/117C_SERVICES_SUB_ACTIVITY_PLAN.md
```

## Approval meaning

Ahmed approved the 117C Services Sub-Activity Selection screen visually.

This is not a main lock yet. The screen remains on the feature branch until PR and merge are explicitly approved.

## Locked visual requirement

117C must continue to reuse the approved 117A setup shell. No new background, shell redesign, large panel geometry change, header/footer redesign, or approved visual direction change is allowed.

## Approved services cards

```text
general           | خدمات عامة                       | General services
maintenance       | صيانة وإصلاح                     | Maintenance & Repair
clinic            | عيادة / مركز طبي                 | Clinic / Medical center
beauty            | صالون / مركز تجميل               | Salon / Beauty center
education         | مركز تعليمي / كورسات             | Education / Courses Center
professional      | مكتب مهني                        | Professional Office
digital_marketing | تسويق وتصميم وخدمات رقمية        | Marketing, Design & Digital Services
other             | نشاط خدمي آخر                    | Other Service Activity
```

## Functional checks already confirmed by Ahmed

```text
python manage.py check
python manage.py test reports.tests_activity_selection
```

## Modules placeholder fix

The modules placeholder must keep activity-aware back behavior:

```text
activity=services    -> /setup/activity/services/?lang=<lang>
activity=commercial  -> /setup/activity/commercial/?lang=<lang>
missing/unknown      -> /setup/activity/?lang=<lang>
```

## Merge rule

Do not merge to main until Ahmed explicitly approves PR/merge after final review.
