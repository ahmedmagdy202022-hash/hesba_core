<!-- DEMO DATA ONLY. The prices in drug_reference_demo.csv are round illustrative figures written for the demo; they are
     NOT official, current or recommended prices. البيانات دي للديمو بس: الأسعار أرقام تقريبية للتوضيح، مش تسعيرة رسمية. -->

# Demo reference drug list

`drug_reference_demo.csv` is a short reference list (122 rows) in Hesba's template layout
(`docs/DRUG_REFERENCE_IMPORT.md`). It is loaded **only into the pharmacy and the clinic demo templates** while
`prepare_demo_templates` builds them (`config/demo_templates.py` calls `pharmacy.demo.load_demo_reference`), with
`source = demo` and `source_version = demo-2026-11`. Real installs never load it. In the clinic (M2, HG-086) two sample
visits are given medicine lines from it (`medical/demo.py`); a prescription never shows the prices.

- Written by hand from general knowledge of products commonly sold in Egyptian pharmacies. No row is copied from any
  dataset or price file.
- **Prices are illustrative round figures, not official prices.** Price date 2026-11-01 for every row.
- Several trade names share an active ingredient and strength, so the till's «بدائل» sheet and the drug page show
  alternatives. The ingredient spellings match the demo shelf (`Paracetamol`, `Amoxicillin`, `Acetylsalicylic acid`,
  `Vitamin C`, `Cholecalciferol`, `Xylometazoline`...).
- `prescription_only` is filled (1) for antibiotics only; every other row leaves it empty (unknown), never guessed.
- `is_drug = 0` for the baby, skin-care and toothpaste rows, which have no active ingredient. Barcodes are empty except
  six in-store codes (2950000001xxx, valid check digits) on those rows.
- The `manufacturer` column is filled only where the brand's maker is well known; otherwise it is left empty.
