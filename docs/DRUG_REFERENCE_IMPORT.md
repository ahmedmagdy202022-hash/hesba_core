# Loading the reference drug list (R1, HG-082)

قايمة الأدوية المرجعية بتتحمّل من ملف بأمر إدارة. القايمة دي للمساعدة في إدخال الأصناف بس: مفيش صنف بيتعمل منها لوحده، ومفيش سعر أو مخزون أو حساب بيتغير.

The reference list lives in `pharmacy.DrugReference`, one global table (the same for every entity). It is loaded with a
management command and is read-only for everything else. The importer never touches items, drug data, prices, stock,
cashboxes or ledgers. No third-party dataset ships in the repository: which list is loaded in production is Ahmed's
decision (licence).

## The command

```
python manage.py import_drug_reference --file PATH --source NAME --source-version TEXT \
    [--price-date YYYY-MM-DD] [--layout auto|hesba|simple] [--dry-run] [--deactivate-missing]
```

- `--source`: a short id for the list, letters, digits, `.`, `_`, `-` (up to 60), e.g. `ahmed-file-2026-11`. Rows are
  upserted by (`source`, `source_ref`), so running the same file again updates the same rows and creates none.
- `--source-version`: the list's version or date as its publisher gives it; stored on every row.
- `--price-date`: the date the prices are from. A `price_date` column in the file wins for its row.
- `--dry-run`: reads and counts, saves nothing.
- `--deactivate-missing`: switches off (`is_active = False`) the rows of **this source** that are not in the file. Other
  sources are untouched. A file with no usable row is refused, so a wrong file cannot switch off a whole source.
- It prints one line in Arabic and English: created, updated (every row found again; only changed rows are rewritten),
  deactivated, skipped (no name, or a repeated `source_ref`), bad barcodes, bad prices.

Files: `.csv` (UTF-8, or Windows Arabic cp1256; comma, semicolon or tab) or `.xlsx` (first sheet). The first non-empty
row is the header; header names are matched ignoring case and spaces.

## Layout 1: the Hesba template (`hesba`)

| Column | Required | Notes |
|---|---|---|
| `source_ref` | no | The row's stable id in the list. Empty: the folded name is used as the id. |
| `name_en` | one of the two | Trade name in English. |
| `name_ar` | one of the two | Trade name in Arabic. |
| `active_ingredient` | no | As the box says it; a combination with `+`, `/` or `,`. |
| `strength` | no | E.g. `500 mg`, `250 mg/5 ml`, `500 مجم`. |
| `form` | no | A form word in Arabic or English (`أقراص`, `tab`, `syrup`, `معلق`...), else left empty. |
| `pack` | no | E.g. `20 tabs`, `120 ml`. |
| `manufacturer` | no | |
| `public_price` | no | Digits, `.` for decimals; Arabic digits are read. Empty or `0` = no price; unreadable or absurd = left empty and counted. |
| `price_date` | no | `YYYY-MM-DD` or `DD/MM/YYYY`. |
| `barcode` | no | EAN-13, EAN-8 or UPC-A with a correct check digit; anything else is left empty and counted. |
| `prescription_only` | no | `1/0`, `yes/no`, `نعم/لا`; empty = unknown (never guessed). |
| `is_drug` | no | `0`/`no`/`لا` for cosmetics, baby milk and the like; empty = a drug. |

A sample with 30 invented rows: `pharmacy/tests_fixtures/drug_reference_sample.csv`.

## Layout 2: a simple list (`simple`)

A file with a name column (`name`, `trade_name`, `drug_name`, `product`, `product_name`, `الاسم`, `اسم الدوا`, `اسم الدواء`,
`الاسم التجاري`), and optionally `price` (`public_price`, `new_price`, `السعر`), `barcode` (`ean`, `gtin`, `الباركود`),
`name_ar`, `active_ingredient` (`ingredient`, `scientific_name`, `generic_name`, `المادة الفعالة`), `manufacturer`
(`company`, `الشركة`) and `source_ref` (`id`). With two columns for one field, the first one listed here wins. A name
in Arabic letters is kept as the Arabic name (`name_ar`) unless the file also has a `name_ar` column.

Strength, form and pack are read from the trade name, and only when the reading is clear:

- strength: a number with `mg`, `mcg`, `g`, `IU`, `%` (or `مجم`, `جم`...), with an optional `/5 ml`; two different
  strengths in one name leave it empty;
- form: one form word in the name (`tab`, `F.C.TABS.`, `caps`, `syrup`, `أقراص`...); two different forms leave it empty;
- pack: `20's`, `20 tabs`, `3 amps`, or a volume such as `120 ml`; grams on a cream are its pack (`30 gm`).

Anything else stays empty. The picker (PR2) shows the row for a person to check before an item is saved.
