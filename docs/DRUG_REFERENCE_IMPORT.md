# Loading the reference drug list (R1, HG-082)

قايمة الأدوية المرجعية بتتحمّل من ملف بأمر إدارة. القايمة دي للمساعدة في إدخال الأصناف بس: مفيش صنف بيتعمل منها لوحده، ومفيش سعر أو مخزون أو حساب بيتغير.

The reference list lives in `pharmacy.DrugReference`, one global table (the same for every entity). It is loaded with a
management command and is read-only for everything else. The importer never touches items, drug data, prices, stock,
cashboxes or ledgers. No third-party dataset ships in the repository: which list is loaded in production is Ahmed's
decision (licence).

## The command

```
python manage.py import_drug_reference --file PATH --source NAME --source-version TEXT \
    [--price-date YYYY-MM-DD] [--layout auto|hesba|simple] [--dry-run] [--deactivate-missing [--force-deactivate]]
```

- `--source`: a short id for the list, letters, digits, `.`, `_`, `-` (up to 60), e.g. `ahmed-file-2026-11`. Rows are
  upserted by (`source`, `source_ref`), so running the same file again updates the same rows and creates none.
- `--source-version`: the list's version or date as its publisher gives it; stored on every row.
- `--price-date`: the date the prices are from. A `price_date` cell in the file wins for its row. See "Importing a newer
  file" for when a date is written.
- `--dry-run`: reads and counts, saves nothing.
- `--deactivate-missing`: switches off (`is_active = False`) the rows of **this source** that are not in the file. Other
  sources are untouched. A file with no usable row is refused, so a wrong file cannot switch off a whole source. A file
  that would switch off **more than half** of the source's active rows is refused too (a truncated or wrong file), unless
  `--force-deactivate` is also given. Exactly half is allowed.
- It prints one line in Arabic and English: created, updated (every row found again; only changed rows are rewritten),
  deactivated, skipped, bad barcodes, bad prices, bad dates. Then, when there are any, a line with the skipped rows by
  reason (`no name`, `duplicate`) with the first three line numbers of each in the file (the header is line 1), and a
  line naming the columns nothing reads ("أعمدة اتجاهلت / ignored columns: …"), so a misspelt header is seen at once.
- A failed write (for example another import of the same source at the same moment) is refused with a clear message
  and saves nothing.

Files: `.csv` or `.txt` (UTF-8, UTF-16 with its byte-order mark, or Windows Arabic cp1256; comma, semicolon or tab) or
`.xlsx` (first sheet). A file that is mostly UTF-8 but has a broken byte is refused with the byte's offset instead of
being read as cp1256 (which would garble every Arabic letter); save it again as UTF-8. The first non-empty row is the
header. Header names are folded before matching: case, Arabic letter forms (`أ/إ/آ` → `ا`, `ة` → `ه`, `ى` → `ي`),
harakat and tatweel are ignored, and spaces, hyphens and punctuation count as one `_` (`Name-EN` = `name_en`,
`Price (EGP)` = `price_egp`, `إسم الدواء` = `اسم الدواء`). Two columns that fold to the same header are refused.

### Which layout

`auto` picks the template when the header has a column only the template has (`strength`, `form`, `pack`,
`source_ref`, `is_drug`, `prescription_only`), even beside a `name` or `product` column; else the simple layout when it
has a name column (`name`, `product`...); else the template when it has `name_en` / `name_ar`. Both layouts accept the
same header aliases (below).

## Layout 1: the Hesba template (`hesba`)

| Column | Required | Notes |
|---|---|---|
| `source_ref` (`id`) | no, **strongly recommended** | The row's stable id in the list. See "Rows without a `source_ref`". |
| `name_en` | one of the two | Trade name in English. A `name` column (or its aliases) is read too: Arabic letters make it `name_ar`. |
| `name_ar` (`arabic_name`, `الاسم العربي`) | one of the two | Trade name in Arabic. |
| `active_ingredient` | no | As the box says it; a combination with `+`, `/` or `,`. |
| `strength` | no | E.g. `500 mg`, `250 mg/5 ml`, `500 مجم`. |
| `form` | no | A form word in Arabic or English (`أقراص`, `tab`, `F.C.Tabs`, `syrup`, `معلق`...), else left empty. |
| `pack` | no | E.g. `20 tabs`, `120 ml`. |
| `manufacturer` | no | |
| `public_price` (`price`, `new_price`, `Price (EGP)`, `السعر`) | no | See "Prices". Empty or `0` = no price; unreadable, ambiguous or past the column's limit (9,999,999,999.99) = left empty and counted. |
| `price_date` | no | `YYYY-MM-DD`, `YYYY/MM/DD` or `DD/MM/YYYY`; anything else is counted under "bad dates". |
| `barcode` (`ean`, `gtin`, `الباركود`) | no | EAN-13, EAN-8 or UPC-A with a correct check digit; anything else is left empty and counted. See "Barcodes". |
| `prescription_only` | no | `1/0`, `yes/no`, `نعم/لا`; empty = unknown (never guessed). |
| `is_drug` | no | `0`/`no`/`لا` for cosmetics, baby milk and the like; empty = a drug. |

`strength`, `form` and `pack`: when the file has the column, its cell is used and an empty cell stays empty (nothing is
guessed). When the file has **no such column at all**, that field is read from the trade name exactly as in the simple
layout below.

A sample with 30 invented rows: `pharmacy/tests_fixtures/drug_reference_sample.csv`.

## Layout 2: a simple list (`simple`)

A file with a name column (`name`, `trade_name`, `drug_name`, `product`, `product_name`, `الاسم`, `اسم الدوا`, `اسم الدواء`,
`الاسم التجاري`), and optionally `price` (`public_price`, `new_price`, `Price (EGP)`, `السعر`), `barcode` (`ean`, `gtin`, `الباركود`),
`name_ar`, `active_ingredient` (`ingredient`, `scientific_name`, `generic_name`, `المادة الفعالة`), `manufacturer`
(`company`, `الشركة`) and `source_ref` (`id`). With two columns for one field, the first one listed here wins. A name
in Arabic letters is kept as the Arabic name (`name_ar`) unless the file also has a `name_ar` column. Other columns
(`price_date`, `strength`... when `--layout simple` is forced) are not read and are named in the "ignored columns" line.

Strength, form and pack are read from the trade name, and only when the reading is clear:

- strength: a number with `mg`, `mcg`, `g`, `IU`, `%` (or `مجم`, `جم`...), with an optional `/5 ml`; two different
  strengths in one name leave it empty;
- form: one form word in the name (`tab`, `F.C.TABS.`, `caps`, `syrup`, `أقراص`...); two different forms leave it empty;
- pack: `20's`, `20 tabs`, `3 amps`, or a volume such as `120 ml`; grams on a cream are its pack (`30 gm`).

A number with a comma right before it is not read: `1,000 IU` leaves the strength empty (a grouped number), and so does
a comma used as a separator, as in `Fab 5mg,10's` (neither `5mg` nor `10's` is read). Write it with a space:
`Fab 5mg 10's`.

Anything else stays empty. The picker (PR2) shows the row for a person to check before an item is saved.

## Rows without a `source_ref`

Give every row a `source_ref` whenever the list has one: it is what keeps a row the same row from one version of the
list to the next. Without one, the id is derived from the row itself: `n:` and the first 24 hex digits of the SHA-1 of
`name|strength|form|pack`, each folded (the folded name as in `name_key`, the strength key such as `500mg`, the form
code such as `tablet`, and the folded pack). So `Samefab 500 mg tab` and `Samefab 1000 mg tab` are two rows, but a row
whose strength, form or pack is corrected in a later version gets a new id (and the old one is switched off by
`--deactivate-missing`). Two rows of one file with the same id (an explicit `source_ref` or a derived one) are never
merged: the first is kept and every later one is skipped as `duplicate`, with its line number in the output.

## Prices

`.` is the decimal point and `,` separates thousands: `1,250.75` is 1250.75, `62.5` and `٦٢٫٥` are 62.50. A currency
word before or after the number is dropped (`EGP`, `LE`, `L.E.`, `ج.م`, `جنيه`). Text with exactly three digits after a
point, such as `1.250` or `12.500`, could be either (1.25 or 1250) and is counted as a bad price; a number cell from
Excel is never ambiguous. `1.250,00` (comma decimals) and `12,5` are bad prices. Excel's float noise
(`27.500000000000004`) is cut at 6 decimals before reading, then the price is rounded to 2 decimals.

## Barcodes

A barcode cell that Excel holds as a number has lost its leading zeros: 7, 11 or 12 digits are padded with zeros to 8,
12 or 13 and checked again (text is never padded). A UPC-A (12 digits) is stored in its EAN-13 form with a leading `0`
(`036000291452` → `0036000291452`), so one product has one barcode string whichever way a list writes it; anything
matching these rows to scanned barcodes must compare in that form.

## Importing a newer file

Running a newer version of the list over the same `--source` updates every row it finds again. The file is the truth
for every field, with these exceptions so that a mistake in the file does not wipe good data:

- a **bad price** cell keeps the stored price (and its date) and is counted under "bad prices"; an empty price cell
  clears the price;
- the **price date** is written only when the row has a price: the row's own `price_date` cell, else `--price-date`;
  run without `--price-date` and with no date cell, a row keeps its stored date, so pass `--price-date` whenever the
  prices in the file are new;
- an **unreadable price date** cell is counted under "bad dates" and leaves that row's date as it was (a new row gets
  no date; `--price-date` is not used in its place);
- an empty `prescription_only` cell sets the row back to unknown: the file is the truth for the flags.
