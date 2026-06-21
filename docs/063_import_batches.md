# 063 Import Batches

Checkpoint: `063_FOUNDATION_IMPORT_BATCHES`

This step adds import batch foundation.

Added:
- ImportBatch
- ImportRaw
- ImportReview
- Import services
- Admin screens
- Migration
- Usage snapshot admin hardening

Rules:
- Raw data stays unchanged.
- Corrected data is stored separately.
- Approved batches must not have invalid rows.
- Imported rows keep batch traceability.
- Preferred approach is go-live data and opening balances.

Next: `064_FOUNDATION_IMPORT_APPLY_SERVICES`
