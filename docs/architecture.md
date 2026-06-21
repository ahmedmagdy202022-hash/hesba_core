# Hesba Core Architecture

Hesba is built as one reusable core with settings and permissions controlling each edition.

## Stack

- Django
- PostgreSQL
- Arabic RTL web interface

## Main domains

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

## Rule

Business logic must live in services. Screens collect input only. Reports are read-only.
