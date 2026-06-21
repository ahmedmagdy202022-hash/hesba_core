# 076 Local Test Instructions

Checkpoint: `076_FOUNDATION_LOCAL_TEST_INSTRUCTIONS`

This step prepares laptop instructions for safe local testing.

## Windows PowerShell

From the project folder:

```powershell
git pull origin main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
.\scripts\dev_safe_test_prep.ps1
```

If the safe prep passes, run:

```powershell
.\scripts\ci_local_check.ps1
```

## Bash or Git Bash

From the project folder:

```bash
git pull origin main
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
bash scripts/dev_safe_test_prep.sh
```

If the safe prep passes, run:

```bash
bash scripts/ci_local_check.sh
```

## What to send back if anything fails

Send the first command output that shows the failure.

## Business cycle protection

The local test path helps verify the Core before live testing of:
Supplier -> Purchase Invoice -> Inventory by Location -> Sales Invoice -> Customer -> Cashbox -> Reports

Next: `077_FOUNDATION_LOCAL_TEST_RESULT_REVIEW`
