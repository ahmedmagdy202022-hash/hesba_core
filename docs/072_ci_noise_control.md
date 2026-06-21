# 072 CI Noise Control

Checkpoint: `072_FOUNDATION_CI_NOISE_CONTROL`

This step reduces repeated GitHub Actions emails while migration fixes are still being completed.

Changed:
- `Django Tests` no longer runs automatically on every direct push to `main`.
- The workflow still runs on pull requests.
- The workflow can still be started manually through `workflow_dispatch`.

Also aligned:
- Usage status snapshot migration options and status choices.

Why:
- Direct-to-main commits during migration repair were creating repeated failed notification emails.
- The migration check stays in the workflow; it was not removed.
- The goal is to stop notification noise while keeping the quality gate available.

Business cycle impact:
- No operational business logic changed.
- Sales, purchases, inventory, customers, suppliers, cashboxes, and reports are not changed by this step.

Next: `073_FOUNDATION_CI_MANUAL_RECHECK`
