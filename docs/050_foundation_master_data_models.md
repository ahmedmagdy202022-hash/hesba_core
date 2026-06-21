# 050 Foundation Models: Master Data

Checkpoint: `050_FOUNDATION_MASTER_DATA_MODELS`

## Scope

This step adds the first master data tables needed before purchases, sales, inventory movements, cashbox movements, and reports.

Added models:

- `Location`
- `Category`
- `Item`
- `Customer`
- `Supplier`
- `Cashbox`

## Business cycle connection

Supplier → Purchase Invoice → Inventory by Location → Sales Invoice → Customer → Cashbox → Reports

This step prepares the base entities for the cycle:

- Suppliers are available for future purchase invoices.
- Locations are available for receiving and selling stock.
- Items are available for purchase lines, sales lines, barcode, and inventory reports.
- Customers are available for future sales invoices and customer payments.
- Cashboxes are available for future real paid amounts only.

## Rules protected in this step

1. No sale logic is implemented.
2. No purchase logic is implemented.
3. No customer due is created from purchases.
4. No supplier due is created from sales.
5. No stock is changed by master data rows.
6. No cashbox balance is changed by invoices in this step.
7. Cashbox balance must later be calculated from opening balance plus real cashbox movements.
8. Item cost fields exist for controlled logic but must be hidden by permissions from unauthorized roles.
9. Reports remain read-only and are not implemented here.

## Item fields

The `Item` model follows the Hesba Core item shape:

- Item code
- Barcode
- Item name
- Category
- Size
- Color
- Unit
- Default sale price
- Default purchase price
- Average cost
- Minimum stock
- Stock tracked flag
- Import batch reference

Search label rule:

`Item_Code - Item_Name - Size - Color`

## Locations

Inventory must later be calculated by:

`Item + Location`

Purchases will need a receiving location.
Sales will need a selling location.
Simple editions can use one default hidden location.

## Cashboxes

The `Cashbox` model is only a money holder definition.

Future cashbox balances must come from:

`Opening Balance + actual Cashbox Movements`

Invoice total must not move cashbox.
Only `Paid_Now` or direct real cash movements can affect cashbox balances.

## Next after merge

Start purchases foundation:

- Purchase invoice header
- Purchase invoice lines
- Receiving location
- Invoice total / paid now / remaining due
- Supplier due from remaining due only
- Inventory movement from purchase lines

Do not start dashboards or reports before transaction structure is stable.
