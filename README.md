# Invoice PDF to Excel Converter

A Streamlit app that turns invoice PDFs into structured invoice records,
stores them in **Supabase as the source of truth** and exports them to Excel.

- Batch upload and processing — large uploads are split internally into safe chunks automatically (no manual batch size)
- Four-page Streamlit UI (sidebar navigation): **Dashboard** (summary cards only), **Upload & Process** (the full workflow: upload → process → review & edit → save → export), **Saved Records** (filters + inline edit + export) and **Settings** (processing options, session controls, debug info)
- Automatic text extraction with fallbacks: **pdfplumber → PyMuPDF → Tesseract OCR → Azure Document Intelligence**
- Rule-based parsing of client, invoice number, date, descriptions, **Sub total** and **Total amount**
- One Excel row per invoice (descriptions combined with newlines in one cell)
- **Upsert** to Supabase keyed on `invoice_no` — no duplicates, existing records updated
- Excel is generated from **Supabase records** (all or filtered), never from session state
- Uploaded PDF files are intentionally **not stored** anywhere

## Features

| Area | Details |
| --- | --- |
| Pages | Sidebar navigation: Dashboard, Upload & Process, Saved Records, Settings (no separate Review & Edit page — the review table and its Save button live on Upload & Process, right after processing) |
| Upload | On Upload & Process section 1: batch PDF upload, drag & drop, "View uploaded files" expander with count and total size |
| Batch processing | Section 2: "Process PDFs" button; batch size is chosen automatically (`≤20 files` → all, average `≤1MB` → 50, `≤5MB` → 25, otherwise 10); valid records upserted immediately; per-file details in "View processing details" expander |
| Parsing | Client, invoice number, date, currency normalisation, line descriptions, **Sub total** (from `Sub Total`/`Subtotal`/`Net Amount` labels) and **Total amount** (from `Total`/`Grand Total`/`Amount Due` labels, never a line-item sum, VAT or subtotal) |
| Editing | Section 3 on Upload & Process: `st.data_editor` with dynamic rows (not hidden in an expander); one row per invoice; users fix values before saving with "Save edited records" directly under the table |
| Validation | Client, Invoice No, Invoice Date, Description, Total amount and Currency required; Sub total numeric if present; missing Invoice No / Total amount → `needs_review` |
| Supabase | `invoice_records` table, upsert on `invoice_no`, records survive app refresh |
| Excel | Section 4 on Upload & Process: "Export all saved records to Excel" (from Supabase) plus a clearly-labelled "Export current extracted rows to Excel (preview)"; Saved Records page adds "Export current filtered records to Excel". Bold blue-header row with white font, frozen top row, wrapped multi-line Description cells with grown row heights, `#,##0.00` numeric Sub total / Total amount |
| Security | Secret key (`sb_secret_...`) read server-side only, never sent to the browser |

### Export format

Columns are exactly, in this order:

```
Client | Invoice No | Invoice Date | Description | Sub total | Total amount | Currency
```

- `Invoice Date` is normalised to `d-mmm-yy` (e.g. `8-Jun-26`).
- `Description` holds all charge descriptions joined with newlines.
- `Sub total` is the amount **before** VAT/tax, `Total amount` the final payable.

Example:

```
Client:      Metshu Exports (Pvt) Ltd
Invoice No:  LKA/CUC/2026/06/1030
Invoice Date: 8-Jun-26
Description:  Import Certificate Charges - 01/02/2026-30/04/2026
              TC Charges - 01/02/2026-30/04/2026
              COI Charges - 01/02/2026-30/04/2026
              GBCOI Charges - 01/02/2026-30/04/2026
Sub total:    1140.00
Total amount: 1345.20
Currency:     Euro
```

(Here VAT of 205.20 is ignored for both Sub total and Total amount.)

## Project structure

```
invoice_converter/
├── app.py                     # Streamlit entry: st.navigation + page wiring
├── pages/
│   ├── dashboard.py           # summary cards + system status only
│   ├── upload_process.py      # the full workflow (sections 1-4)
│   ├── saved_records.py       # load/filter/edit/export persisted records
│   └── settings.py            # OCR/Azure toggles, session, debug info
├── requirements.txt
├── .env.example
├── .gitignore
├── models/
│   └── schemas.py             # ParsedInvoice dataclass (one per invoice)
├── services/
│   ├── pdf_extractor.py       # pdfplumber -> PyMuPDF -> OCR -> Azure chain
│   ├── ocr_service.py         # pytesseract on PyMuPDF-rendered pages
│   ├── azure_invoice_service.py  # optional prebuilt-invoice integration
│   ├── invoice_parser.py      # rule-based invoice-level parsing
│   ├── processing.py          # auto batch chunking + processing + upsert
│   ├── excel_exporter.py      # openpyxl workbook generation
│   ├── supabase_service.py    # upsert / query of invoice_records
│   └── validators.py          # per-record validation + review flags
├── utils/
│   ├── config.py              # secrets from Streamlit secrets / .env
│   ├── logging_config.py
│   ├── file_utils.py
│   └── ui_helpers.py          # session state, summary cache, record <-> row
└── sql/
    └── schema.sql             # run once in the Supabase SQL editor
```

## Local setup

### 1. Prerequisites

- Python 3.9+
- `tesseract` installed on the machine (only needed for the OCR fallback):
  - Ubuntu/Debian: `sudo apt-get install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: install from https://github.com/UB-Mannheim/tesseract/wiki

### 2. Install and run

```bash
cd invoice_converter
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your values
streamlit run app.py
```

Open http://localhost:8501.

### 3. Configure optional components

| Component | Config | Notes |
| --- | --- | --- |
| Supabase (recommended) | `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (or legacy `SUPABASE_SERVICE_ROLE_KEY`) | Without it the app still parses and previews records, but Excel export (from stored records) and saving are unavailable |
| Azure Document Intelligence (optional) | `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `AZURE_DOCUMENT_INTELLIGENCE_KEY` | Enhanced fallback for scanned PDFs |

## Supabase setup

Skipping Supabase for now? Just leave `ENABLE_SUPABASE_SAVE=false` (the
default). Set it to `true` and add the credentials below to enable saving,
loading and Excel export from stored records.

1. Create a project at https://supabase.com.
2. **Database schema**: open the **SQL Editor** and run the contents of
   [`sql/schema.sql`](sql/schema.sql). This creates the `invoice_records`
   table (with the `invoice_no` uniqueness constraint used by upsert), the
   `updated_at` trigger, RLS policies and indexes.
   - The file also includes the idempotent `ALTER TABLE ... ADD COLUMN IF NOT
     EXISTS` migration for tables created by an older version, and the legacy
     `amount` / `currancy` columns (if any) are left untouched and ignored.
3. **API keys**: go to **Project Settings → API** (or **Settings → API Keys**) and copy:
   - `Project URL` → `SUPABASE_URL`
   - The server-side **Secret key** (`sb_secret_...`, under **Secret keys**) →
     `SUPABASE_SECRET_KEY` (or the legacy `service_role` JWT under **Legacy API
     Keys** → `SUPABASE_SERVICE_ROLE_KEY`). The app accepts either name; the
     legacy `service_role` key is deprecated by Supabase at the end of 2026.
   - Do not use `SUPABASE_JWKS_URL` - it is only for verifying user session
     JWTs, not a database credential.

Uploaded PDF files are **not** stored — only the extracted records, so no
storage bucket is needed.

## Typical workflow (700 invoices)

1. **Upload & Process → 1. Upload PDFs**: choose invoice PDFs (20–50 at a
   time is fine; the app handles more in one go too).
2. **2. Process PDFs**: press **Process PDFs**. Valid records are parsed and
   upserted immediately (auto chunking if the upload is large); incomplete
   records stay in the preview below marked "Needs review".
3. **3. Review & edit extracted rows**: correct the rows in the `data_editor`,
   then press **Save edited records** directly under the table — the upsert
   never duplicates an `invoice_no`.
4. Repeat uploads / processing across multiple sessions; records persist in
   Supabase. Use **Clear session data** in Settings when starting fresh.
5. **4. Export to Excel**: export all saved records (from Supabase) or the
   current extracted rows as a labelled preview.
6. **Saved Records** page: load records with filters (status, reviewed,
   client, invoice number, date range), inline-edit and save changes back,
   or export the filtered set to Excel.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `ENABLE_SUPABASE_SAVE` | optional | Master switch for Supabase saving. Default `false`. Set to `true` plus the credentials below to enable saving/loading/export |
| `SUPABASE_URL` | for saving | Supabase project URL |
| `SUPABASE_SECRET_KEY` | for saving | Server-side Secret key (`sb_secret_...`, Settings → API Keys → Secret keys). Preferred over the legacy key |
| `SUPABASE_SERVICE_ROLE_KEY` | for saving | Legacy `service_role` JWT (Settings → API Keys → Legacy API Keys). Still accepted as a fallback, deprecated by Supabase end of 2026 |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | optional | Azure Form/Document Intelligence endpoint |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | optional | Azure API key |

## How it works

1. **Extraction chain** (`services/pdf_extractor.py`): tries pdfplumber first;
   falls back to PyMuPDF, then Tesseract OCR (pages rasterised at 200 DPI),
   then Azure Document Intelligence.
2. **Parsing** (`services/invoice_parser.py`): one `ParsedInvoice` per PDF.
   - `extract_descriptions` returns only the charge descriptions (summary /
     bank / footer lines excluded).
   - `extract_sub_total` looks for `Sub Total` / `Subtotal` / `Net Amount`
     labels and ignores VAT, tax and the grand total.
   - `extract_total_amount` looks for `Total` / `Grand Total` / `Amount Due` /
     `Balance Due` labels and picks the final payable amount — never a line
     item, the subtotal or VAT.
3. **Upsert**: after processing, valid records are upserted on `invoice_no`;
   incomplete records (e.g. missing total) stay in the preview flagged
   `needs_review` until manually corrected and saved. The `reviewed` flag is
   human-only: it is never set by processing, only by ticking the **Reviewed**
   checkbox on the Saved Records page, and re-processing a PDF preserves an
   existing `reviewed` value.
4. **Load**: the Saved Records page loads any subset of the stored records
   (filters on status, reviewed, client, invoice number, date range).
5. **Export**: Excel is generated from the Supabase records via `openpyxl` —
   one row per invoice, bold header, frozen first row, wrapped Description
   cells with row heights grown to fit, numeric `#,##0.00` Sub total and
   Total amount. The "Export current extracted rows to Excel (preview)"
   button is the only export that uses session rows instead of stored records.

## Notes on legacy columns

If an earlier version of the schema stored `amount` and `currancy` columns,
they are kept untouched. Internally the app uses the clean field names
`client`, `invoice_no`, `invoice_date`, `description`, `sub_total`,
`total_amount`, `currency` and exports the final headers `Client`, `Invoice
No`, `Invoice Date`, `Description`, `Sub total`, `Total amount`, `Currency`
— never `Invo No` / `Invo Date` / `amount` / `currancy`.

## Debugging

- Logs are written to stdout by `utils/logging_config.py`.
- Use the **View processing details** expander (section 2) to see the
  extraction method and a per-file status/message for each PDF.
- On Streamlit Cloud, check **Settings → App logs**.
- **500 error on Cloud startup** (`GZipResponder.__init__() missing ...
  'thread_minimum_size'`): a too-new `starlette` breaks Streamlit. The
  requirements are pinned to the known-good combo (`starlette==1.3.1` with
  `streamlit==1.61.0`); keep those pins when updating.