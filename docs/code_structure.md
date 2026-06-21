# Code Structure Lock

Project root:

- manage.py
- requirements.txt
- config
- accounts
- permissions
- settings_core
- master_data
- inventory
- purchases
- sales
- cashboxes
- reports
- closing
- audit
- imports
- barcode
- templates
- static
- docs

Each business app should include:

- models
- services
- selectors
- forms
- views
- urls
- permissions
- tests

Main implementation rule:

Screens collect input only. Business operations must go through service functions.

First services:

- create_purchase_invoice
- create_sales_invoice
- create_customer_payment
- create_supplier_payment
- check_stock_available
- create_cashbox_movement
- create_audit_log

Reports must be read-only.
