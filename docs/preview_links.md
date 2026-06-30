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
- Service sub-activity page remains a placeholder only and contains no business logic.

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
- No PR opened.
- No merge performed.
- `/setup/modules/` is a safe placeholder only; modules business logic is out of scope for 117B.
- 117B reuses the approved 117A visual shell and does not add or generate background assets.
