# 061 Post Closing Adjustment Services

Checkpoint: `061_FOUNDATION_POST_CLOSING_ADJUSTMENT_SERVICES`

## Scope

This step adds controlled services for post-closing adjustment documents.

Added:

- Create post-closing adjustment service
- Post post-closing adjustment service
- Cancel post-closing adjustment service
- Audit log entries for create, post, and cancel
- Closed-period validation
- Current-open-period validation for the adjustment date

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Post-closing adjustments protect closed periods while allowing corrections to be documented and posted through the current open period.

## Rules

- Post-closing adjustment must reference a closed period.
- Adjustment date must be inside an open period.
- The adjustment document itself does not directly change stock, customer, supplier, or cashbox balances.
- Actual corrections must be posted through the relevant controlled transaction service in the current open period.
- Cancel requires a reason.
- Audit log is recorded.

## Why this matters

Closed periods stay readable and stable. Corrections remain traceable without rewriting old transaction history.

## Next after merge

`062_FOUNDATION_USAGE_STATUS`
