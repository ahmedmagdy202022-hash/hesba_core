# Preview Links

## 117A Activity Selection Screen

Branch: `feature/117a-activity-selection-screen`  
Route: `/setup/activity/`  
Recommended port: `8010`

Arabic:
- `http://127.0.0.1:8010/setup/activity/?lang=ar`

English:
- `http://127.0.0.1:8010/setup/activity/?lang=en`

Placeholder navigation targets for 117A testing only:
- `http://127.0.0.1:8010/setup/activity/service/?lang=ar`

Notes:
- No PR opened.
- No merge performed.
- Service legacy placeholder route remains available at `/setup/activity/service/`.

## 117B Commercial Sub-Activity Selection

Branch: `feature/117b-commercial-subactivity-selection`  
Route: `/setup/activity/commercial/`  
Recommended port: `8010`

Arabic:
- `http://127.0.0.1:8010/setup/activity/commercial/?lang=ar`

English:
- `http://127.0.0.1:8010/setup/activity/commercial/?lang=en`

Temporary 117B next target placeholder:
- `http://127.0.0.1:8010/setup/modules/?lang=ar&activity=commercial&sub_activity=retail`
- `http://127.0.0.1:8010/setup/modules/?lang=en&activity=commercial&sub_activity=retail`

Notes:
- 117B has been merged into main.
- `/setup/modules/` is a safe placeholder only; modules business logic is out of scope for 117B.
- 117B reuses the approved 117A visual shell and does not add or generate background assets.

## 117C Services Sub-Activity Selection

Branch: `feature/117c-services-subactivity-selection`  
Route: `/setup/activity/services/`  
Recommended port: `8010`

Arabic:
- `http://127.0.0.1:8010/setup/activity/services/?lang=ar`

English:
- `http://127.0.0.1:8010/setup/activity/services/?lang=en`

Temporary 117C next target placeholder:
- `http://127.0.0.1:8010/setup/modules/?lang=ar&activity=services&sub_activity=general`
- `http://127.0.0.1:8010/setup/modules/?lang=en&activity=services&sub_activity=general`

Notes:
- No PR opened.
- No merge performed.
- 117C reuses the approved 117A setup shell.
- No modules business logic is implemented in 117C.
- Replace `127.0.0.1` with the forwarded Codespaces host when previewing through GitHub Codespaces.

## 118 Modules Selection

Branch: `feature/118-modules-selection`  
Route: `/setup/modules/`  
Recommended port: `8010`

Arabic commercial:
- `http://127.0.0.1:8010/setup/modules/?lang=ar&activity=commercial&sub_activity=retail`

English commercial:
- `http://127.0.0.1:8010/setup/modules/?lang=en&activity=commercial&sub_activity=retail`

Arabic services:
- `http://127.0.0.1:8010/setup/modules/?lang=ar&activity=services&sub_activity=general`

English services:
- `http://127.0.0.1:8010/setup/modules/?lang=en&activity=services&sub_activity=general`

Safe review placeholder target:
- `http://127.0.0.1:8010/setup/review/?lang=ar&activity=commercial&sub_activity=retail&modules=sales_operations,items_services,cashboxes,reports`

Notes:
- No PR opened.
- No merge performed.
- 118 replaces the old `/setup/modules/` placeholder with the real modules selection screen.
- 118 reuses the approved 117A setup shell and adds only inner module card/toggle layout.
- Replace `127.0.0.1` with the forwarded Codespaces host when previewing through GitHub Codespaces.

## 119 Review Setup

Branch: `feature/119-review-setup`  
Route: `/setup/review/`  
Recommended port: `8010`

Arabic review:
- `http://127.0.0.1:8010/setup/review/?lang=ar&activity=commercial&sub_activity=retail&modules=sales_operations,items_services,cashboxes,reports,pdf_printing`

English review:
- `http://127.0.0.1:8010/setup/review/?lang=en&activity=commercial&sub_activity=retail&modules=sales_operations,items_services,cashboxes,reports,pdf_printing`

Complete placeholder:
- `http://127.0.0.1:8010/setup/complete/?lang=ar`
- `http://127.0.0.1:8010/setup/complete/?lang=en`

Notes:
- No PR opened.
- No merge performed.
- 119 replaces the `/setup/review/` placeholder with the real review setup screen.
- `/setup/complete/` is a safe placeholder only; no real setup activation or database save is implemented.
- 119 reuses the approved 117A setup shell and adds only inner review summary layout.
- Replace `127.0.0.1` with the forwarded Codespaces host when previewing through GitHub Codespaces.
