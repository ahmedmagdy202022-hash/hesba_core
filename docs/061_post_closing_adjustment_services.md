# 061 Post Closing Adjustment Services

Checkpoint: `061_FOUNDATION_POST_CLOSING_ADJUSTMENT_SERVICES`

This step adds controlled services for correction documents related to closed periods.

Added:

- Create service
- Post service
- Cancel service
- Audit records
- Closed period validation
- Open period validation

Business cycle:

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

Rules:

- The correction document must reference a closed period.
- The correction date must be in an open period.
- The document itself does not move stock, customer, supplier, or cashbox balances.
- Real corrections must use controlled transaction services in the open period.
- Cancel requires a reason.

Next: `062_FOUNDATION_USAGE_STATUS`
