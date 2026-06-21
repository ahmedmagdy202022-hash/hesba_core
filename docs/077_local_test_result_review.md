# 077 Local Test Result Review

Checkpoint: `077_FOUNDATION_LOCAL_TEST_RESULT_REVIEW`

This file defines how to read the local test output when the laptop test is run later.

## Safe prep output review

Expected order:
1. Django system check finishes without errors.
2. Migration list appears.
3. Migration plan appears.
4. Migration drift check finishes without creating new migrations.

If the safe prep fails:
- Stop immediately.
- Do not run the full local CI script yet.
- Copy the first error block only.

## Full local CI output review

Expected order:
1. Django system check passes.
2. Migration drift check passes.
3. Django tests run.
4. Test result is OK.

If tests fail:
- Copy the first failing test name.
- Copy the first traceback only.
- Do not change code manually in the laptop copy unless requested.

## What this protects

This keeps the test sequence safe before testing the full Core business cycle:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

## Next

`078_FOUNDATION_ADMIN_SMOKE_TEST_PLAN`
