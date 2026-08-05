# Invoice PDF to Excel Converter

A production-ready Streamlit MVP that turns invoice PDFs into editable rows,
saves them to Supabase (PostgreSQL + Storage) and exports them to Excel.

- Upload one or many invoice PDFs (drag & drop)
- Automatic text extraction with fallbacks: **pdfplumber → PyMuPDF → Tesseract OCR → Azure Document Intelligence**
- Rule-based parsing of client name, invoice number, invoice date, currency and line items
- Editable results table with validation warnings
- Duplicate invoice detection by invoice number
- Save invoices + line items to Supabase, original PDFs to Supabase Storage
- Clean Excel export via `openpyxl` with a fixed column layout

## Features

| Area | Details |
| --- | --- |
| Upload | Batch PDF upload, drag & drop, per-file status (Pending / Processing / Success / Needs review / Failed) |
| Extraction | Priority chain with a debug "extracted text" expander per file |
| Parsing | Client, invoice ref, date, currency normalisation (€/EUR → Euro, $/USD → USD, Rs/LKR → LKR, ...) |
| Editing | `st.data_editor` with dynamic rows; users fix values before saving/exporting |
| Validation | Warns on missing client, invoice no, date, currency, amount, description (export is still allowed) |
| Supabase | `invoices` + `invoice_lines` tables, PDFs in the `invoice-pdfs` bucket, duplicate detection |
| Excel | Bold header, auto column width, `#,##0.00` amounts, frozen top row |
| Security | Service role key read server-side only, never sent to the browser |

### Export format

Columns are exactly:

```
Client | Invo No | Invo Date | description | amount | currancy
```

`Invo Date` is normalised to `d-mmm-yy` (e.g. `8-Jun-26`), `amount` to a number.

## Project structure

```
invoice_converter/
├── app.py                     # Streamlit UI (thin, orchestration only)
├── requirements.txt
├── .env.example
├── .gitignore
├── models/
│   └── schemas.py             # InvoiceLine, ParsedInvoice dataclasses
├── services/
│   ├── pdf_extractor.py       # pdfplumber -> PyMuPDF -> OCR -> Azure chain
│   ├── ocr_service.py         # pytesseract on PyMuPDF-rendered pages
│   ├── azure_invoice_service.py  # optional prebuilt-invoice integration
│   ├── invoice_parser.py      # rule-based metadata + line item parsing
│   ├── excel_exporter.py      # openpyxl workbook generation
│   ├── supabase_service.py    # Postgres inserts + storage uploads
│   └── validators.py          # per-row validation warnings
├── utils/
│   ├── config.py              # secrets from Streamlit secrets / .env
│   ├── logging_config.py
│   └── file_utils.py
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
| Supabase (required for saving) | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_BUCKET` | Without these the app still processes and exports Excel |
| Azure Document Intelligence (optional) | `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `AZURE_DOCUMENT_INTELLIGENCE_KEY` | Used as an enhanced fallback for scanned PDFs |

## Supabase setup

Skipping Supabase for now? Just leave `ENABLE_SUPABASE_SAVE=false` (the
default) — the app works fully as a local PDF → Excel converter. Come back to
this section when you want to enable saving.

1. Create a project at https://supabase.com.
2. **Database schema**: open the **SQL Editor** and run the contents of
   [`sql/schema.sql`](sql/schema.sql). This creates the `invoices` and
   `invoice_lines` tables, RLS policies and the `invoice-pdfs` storage bucket.
   - The bucket can also be created manually in **Storage** (name:
     `invoice-pdfs`, public).
3. **API keys**: go to **Project Settings → API** and copy:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` (keep it server-side only!)

## Deployment to Streamlit Community Cloud

1. Push the project to a GitHub repository (do **not** commit `.env` or
   `secrets.toml`).
2. Go to https://share.streamlit.io → **Create app** → select the repository,
   branch, main file `invoice_converter/app.py`... or simpler: add a
   `streamlit_app.py`/use the repo root. If you keep the folder layout above,
   set the main file to `invoice_converter/app.py`.
3. Under **Advanced settings → Secrets**, paste:

```toml
SUPABASE_URL="https://xxxx.supabase.co"
SUPABASE_SERVICE_ROLE_KEY="eyJ..."
SUPABASE_BUCKET="invoice-pdfs"

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://xxxx.cognitiveservices.azure.com/"
AZURE_DOCUMENT_INTELLIGENCE_KEY="..."
```

   (The Azure block is optional.)
4. Deploy. `requirements.txt` is picked up automatically.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `ENABLE_SUPABASE_SAVE` | optional | Master switch for saving to Supabase. Default `false` (app runs purely as a local PDF → Excel converter). Set to `true` plus the credentials below to enable saving |
| `SUPABASE_URL` | for saving | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | for saving | Service role key (server-side only) |
| `SUPABASE_BUCKET` | optional | Storage bucket name, default `invoice-pdfs` |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | optional | Azure Form/Document Intelligence endpoint |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | optional | Azure API key |

## How it works

1. **Extraction chain** (`services/pdf_extractor.py`): tries pdfplumber first;
   if the text is empty or shorter than 40 characters it falls back to
   PyMuPDF, then Tesseract OCR (pages rasterised at 200 DPI), then Azure
   Document Intelligence (when enabled and configured).
2. **Parsing** (`services/invoice_parser.py`): rule-based extraction of
   client name (labels, company suffixes, position before the invoice ref),
   invoice number (`Invoice ref:`, `Inv No:`, `Ref:`, ...), invoice date
   (ISO, `dd/mm/yyyy`, `8-Jun-26`), currency (most frequent token) and line
   items (trailing amount with optional currency token, quantity patterns
   like `2 x 100.00`, exclusions for subtotal/VAT/total/bank/payment terms).
3. **Review**: all rows land in an editable table; warnings flag incomplete
   rows. Nothing is saved or exported without the user's explicit action.
4. **Save**: PDFs are uploaded to the `invoice-pdfs` bucket, invoices and
   line items inserted (duplicates by invoice number are skipped with a
   warning).
5. **Export**: `openpyxl` workbook with the fixed 6-column layout, bold
   headers, frozen first row and number-formatted amounts.

## Known limitations

- Parsing is heuristic: unusually formatted invoices may need manual
  corrections in the editor before saving/exporting.
- OCR accuracy depends on the local `tesseract` install and PDF scan quality.
- Dates like `8/6/26` are interpreted as day-first (common for EU invoices).
- Files are held in memory while the app runs - for very large batches
  prefer the exported Excel file for storage.
- The `currancy` column name (spelling) is intentional and matches the
  required export format.
- Excel export and local processing work without any cloud setup; only the
  "Save to Supabase" section requires credentials.

## Debugging

- Logs are written to stdout by `utils/logging_config.py`.
- Use the **Debug extracted text** expander in the app to see exactly what
  each PDF produced before parsing.
- On Streamlit Cloud, check **Settings → App logs**.
- **500 error on Cloud startup** (`GZipResponder.__init__() missing ...
  'thread_minimum_size'`): a too-new `starlette` breaks Streamlit. The
  requirements are pinned to the known-good combo (`starlette==1.3.1` with
  `streamlit==1.61.0`); keep those pins when updating.
