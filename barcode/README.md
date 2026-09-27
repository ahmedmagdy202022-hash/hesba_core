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
