# Barcode app (BARCODE-001)

- `symbology.py`: EAN-13 and Code 128 drawn as SVG, with no third-party library. Verified against published check digits, and decoded by an independent reader (ZXing).
- `services.py`:
  - `assign_missing_barcodes` gives items without a barcode a shop-internal EAN-13 (GS1 prefix 20) and audits it;
  - `item_catalog` is the scan lookup used by the invoice forms (never includes cost).
- Screens:
  - `/barcode/labels/` picks items and copies (`barcode.print_labels`);
  - `/barcode/labels/print/` is the A4 24-up or 50×25 mm roll label sheet;
  - `/barcode/generate/` (`master_data.manage_items`).
- `static/hesba/js/invoice_form.js`: the scan box, add-line button and live total on the sales and purchase invoice forms.
- LABEL-003 (designs + PDF):
  - `designs.py`: shop-made label designs (any size; roll or A4 grid with margins and gaps; text sizes; default lines), stored as JSON in the `labels.designs` SystemSetting, audited. Keys are `d<id>` and plug into `label_templates.spec`.
  - `/barcode/designs/` lists them; `/barcode/designs/new/` and `/<id>/` edit with a live preview (`static/hesba/js/label_designer.js`), `master_data.manage_items`.
  - The print page's **Download PDF** (`static/hesba/js/label_pdf.js`) draws each page on a canvas (600 dpi roll, 300 dpi A4) and writes a PDF at the exact paper size. No PDF library and no server work; bars are whole pixels per module.
