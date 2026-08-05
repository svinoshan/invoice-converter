"""Streamlit UI for the Invoice PDF to Excel Converter.

This module only orchestrates the UI: file upload, processing pipeline,
editable results table, Supabase save and Excel export. All business logic
lives in the ``services`` and ``models`` packages so it stays testable.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from models.schemas import InvoiceLine
from services import (
    excel_exporter,
    invoice_parser,
    pdf_extractor,
    supabase_service,
    validators,
)
from services.azure_invoice_service import is_configured as azure_is_configured
from utils.file_utils import get_export_filename, human_readable_bytes
from utils.logging_config import get_logger

logger = get_logger("app")

EXPORT_COLUMNS = excel_exporter.EXPORT_COLUMNS

st.set_page_config(page_title="Invoice PDF to Excel Converter", layout="wide")

SS = st.session_state


def _init_state() -> None:
    SS.setdefault("files", [])          # [{"name": str, "data": bytes}]
    SS.setdefault("statuses", {})       # name -> {"status", "message", "method"}
    SS.setdefault("parsed", {})         # name -> ParsedInvoice
    SS.setdefault("row_meta", [])       # per editor row: {"file": name}
    SS.setdefault("save_log", [])       # per invoice save result


_init_state()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _render_sidebar() -> Dict[str, bool]:
    """Render the sidebar and return the selected processing options."""
    azure_available = azure_is_configured()

    with st.sidebar:
        st.header("Invoice PDF to Excel Converter")
        st.caption(
            "Upload one or more invoice PDFs. The app extracts the client, "
            "invoice number, date, currency and line items, then lets you "
            "review, fix, save to Supabase and export to Excel."
        )

        st.divider()
        st.subheader("Processing mode")
        ocr_enabled = st.checkbox(
            "OCR fallback",
            value=True,
            help="Use Tesseract OCR when the PDF has no selectable text.",
        )
        azure_enabled = st.checkbox(
            "Azure Document Intelligence",
            value=azure_available,
            disabled=not azure_available,
            help="Use the Azure prebuilt-invoice model for scanned PDFs.",
        )
        if azure_available:
            st.caption("Azure Document Intelligence is configured and enabled.")
        else:
            st.caption(
                "Azure is optional - set AZURE_DOCUMENT_INTELLIGENCE_* variables "
                "to enable it."
            )

        st.divider()
        if st.button("Clear session", width="stretch"):
            for key in ("files", "statuses", "parsed", "row_meta", "save_log"):
                SS.pop(key, None)
            st.rerun()
        st.download_button(
            "Download empty Excel template",
            data=excel_exporter.generate_template_bytes(),
            file_name="invoice_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

        st.divider()
        if supabase_service.is_enabled() and supabase_service.is_configured():
            st.caption("Supabase: connected")
            with st.expander("Existing invoices in database"):
                existing = supabase_service.get_existing_invoices()
                if existing:
                    st.write(f"{len(existing)} invoice(s) already stored:")
                    st.code("\n".join(existing))
                else:
                    st.write("No invoices stored yet (or database unreachable).")
        elif supabase_service.is_enabled():
            st.caption(
                "Supabase: enabled but SUPABASE_URL / "
                "SUPABASE_SERVICE_ROLE_KEY are missing."
            )
        else:
            st.caption(
                "Supabase saving is disabled - the app runs as a local "
                "PDF-to-Excel converter. Set ENABLE_SUPABASE_SAVE=true and add "
                "the SUPABASE_* credentials to enable it."
            )

    return {"ocr_enabled": ocr_enabled, "azure_enabled": azure_enabled}


# ---------------------------------------------------------------------------
# 1. Upload
# ---------------------------------------------------------------------------


def _prune_stale_entries() -> None:
    """Drop statuses/parsed results for files that were removed by the user."""
    names = {file_item["name"] for file_item in SS.files}
    for store in (SS.statuses, SS.parsed):
        for key in list(store):
            if key not in names:
                store.pop(key, None)


def _render_upload() -> None:
    st.header("1. Upload PDFs")
    uploaded = st.file_uploader(
        "Choose invoice PDF files (drag and drop is supported)",
        type=["pdf"],
        accept_multiple_files=True,
        key="file_uploader",
    )
    if uploaded is not None:
        SS.files = [{"name": item.name, "data": item.getvalue()} for item in uploaded]
        _prune_stale_entries()

    if SS.files:
        st.caption(f"{len(SS.files)} PDF(s) selected.")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "File": file_item["name"],
                        "Size": human_readable_bytes(len(file_item["data"])),
                    }
                    for file_item in SS.files
                ]
            ),
            hide_index=True,
            width="stretch",
        )


# ---------------------------------------------------------------------------
# 2. Processing
# ---------------------------------------------------------------------------


def _process_all(options: Dict[str, bool]) -> None:
    """Run extraction + parsing for every uploaded file."""
    files = list(SS.files)
    if not files:
        st.warning("Please upload at least one PDF first.")
        return

    progress = st.progress(0.0, text="Processing PDFs...")
    for index, file_item in enumerate(files, start=1):
        name = file_item["name"]
        SS.statuses[name] = {"status": "Processing", "message": "Extracting text...", "method": ""}
        try:
            text, method, azure_fields = pdf_extractor.extract_text_from_pdf(
                file_item["data"],
                azure_enabled=options["azure_enabled"],
                ocr_enabled=options["ocr_enabled"],
            )
            parsed = invoice_parser.parse_invoice(
                text, file_name=name, extraction_method=method, hint=azure_fields
            )
            SS.parsed[name] = parsed

            lines_count = len(parsed.lines)
            if method == "none":
                status, message = "Failed", "Could not extract usable text from this PDF."
            elif not parsed.lines or parsed.warnings:
                status = "Needs review"
                message = (
                    f"Extracted with {method} - {lines_count} line(s); "
                    "check the warnings below."
                )
            else:
                status = "Success"
                message = f"Extracted {lines_count} line(s) with {method}."
            SS.statuses[name] = {"status": status, "message": message, "method": method}
        except Exception as exc:  # noqa: BLE001 - show a friendly message
            logger.exception("Failed to process %s", name)
            SS.statuses[name] = {
                "status": "Failed",
                "message": f"Error: {exc}",
                "method": "",
            }
        progress.progress(index / len(files), text=f"Processed {name}")
    progress.empty()


def _render_status_table() -> None:
    if not SS.statuses:
        return
    rows = []
    for file_item in SS.files:
        info = SS.statuses.get(file_item["name"])
        if info:
            rows.append(
                {
                    "File": file_item["name"],
                    "Status": info["status"],
                    "Method": info["method"],
                    "Message": info["message"],
                }
            )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# 3. Results / editor
# ---------------------------------------------------------------------------


def _build_editor_frame() -> pd.DataFrame:
    """Build the editor dataframe and refresh the per-row file metadata."""
    records: List[Dict] = []
    meta: List[Dict] = []
    for name, parsed in SS.parsed.items():
        file_records = invoice_parser.flatten_invoice(parsed, file_name=name)
        records.extend(file_records)
        count = len(parsed.lines) if parsed.lines else 1
        meta.extend([{"file": name}] * count)
    SS.row_meta = meta
    if not records:
        return pd.DataFrame(columns=EXPORT_COLUMNS)
    return pd.DataFrame(records, columns=EXPORT_COLUMNS)


def _render_results() -> Optional[pd.DataFrame]:
    st.header("3. Review & edit extracted rows")

    if not SS.parsed:
        st.caption("Process the PDFs first - extracted rows will appear here.")
        return None

    frame = _build_editor_frame()
    if frame.empty:
        st.info(
            "No rows were extracted from the processed files. "
            "Check the debug text below or try OCR mode."
        )
        return frame

    edited = st.data_editor(
        frame,
        key="invoice_editor",
        num_rows="dynamic",
        width="stretch",
        column_config={
            "Invo Date": st.column_config.TextColumn("Invo Date"),
            "amount": st.column_config.NumberColumn("amount", format="%.2f"),
        },
    )

    records = edited.to_dict("records")
    validation = validators.validate_rows(records)
    if validation:
        st.warning(f"{len(validation)} row(s) need attention before saving/exporting:")
        for item in validation[:25]:
            st.markdown(f"- **Row {item['row']}**: {', '.join(item['issues'])}")
    else:
        st.success("All rows look complete.")

    total_amount = 0.0
    for record in records:
        try:
            total_amount += float(record.get("amount") or 0)
        except (TypeError, ValueError):
            continue

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("PDFs uploaded", len(SS.files))
    col2.metric(
        "Successfully processed",
        sum(1 for status in SS.statuses.values() if status["status"] == "Success"),
    )
    col3.metric(
        "Needs review",
        sum(1 for status in SS.statuses.values() if status["status"] == "Needs review"),
    )
    col4.metric("Rows extracted", len(records))
    col5.metric("Total amount", f"{total_amount:,.2f}")

    with st.expander("Debug extracted text"):
        for name, parsed in SS.parsed.items():
            st.markdown(f"**{name}** (method: {parsed.extraction_method})")
            st.code((parsed.raw_text or "")[:20000])

    return edited


# ---------------------------------------------------------------------------
# 4. Save to Supabase
# ---------------------------------------------------------------------------


def _first_nonempty(rows: List[Dict], key: str) -> str:
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _align_meta(meta: List[Dict], size: int) -> List[Dict]:
    """Align per-row file metadata with the (possibly edited) table size."""
    aligned = list(meta)
    while len(aligned) < size:
        aligned.append({"file": "manual"})
    return aligned[:size]


def _run_save(edited: pd.DataFrame) -> None:
    """Save the edited rows (grouped by invoice) to Supabase."""
    if not supabase_service.is_configured():
        st.error(
            "Supabase is not configured. Add SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY to your secrets and restart."
        )
        return

    records = edited.to_dict("records")
    meta = _align_meta(SS.row_meta, len(records))

    # Group rows by (invoice number, source file).
    groups: Dict[tuple, List[int]] = {}
    for index, (record, item) in enumerate(zip(records, meta)):
        invoice_no = str(record.get("Invo No") or "").strip()
        key = (invoice_no, item.get("file", "manual"))
        groups.setdefault(key, []).append(index)

    save_log = SS.save_log
    saved_in_session: set = set()
    uploaded_urls: Dict[str, str] = {}
    summary = {"saved": 0, "duplicates": 0, "skipped": 0, "errors": 0}

    for (invoice_no, file_name), indices in groups.items():
        rows = [records[i] for i in indices]

        if not invoice_no:
            summary["skipped"] += 1
            for index in indices:
                save_log.append(
                    {
                        "File": file_name,
                        "Invo No": "",
                        "Result": "Skipped",
                        "Message": f"Row {index + 1}: invoice number is empty",
                    }
                )
            continue

        try:
            if invoice_no in saved_in_session:
                summary["duplicates"] += 1
                save_log.append(
                    {
                        "File": file_name,
                        "Invo No": invoice_no,
                        "Result": "Duplicate",
                        "Message": "Already saved in this session",
                    }
                )
                continue

            if supabase_service.check_duplicate_invoice(invoice_no):
                summary["duplicates"] += 1
                save_log.append(
                    {
                        "File": file_name,
                        "Invo No": invoice_no,
                        "Result": "Duplicate",
                        "Message": "Invoice number already exists in the database",
                    }
                )
                continue

            pdf_url = ""
            pdf_name = ""
            if file_name != "manual":
                if file_name not in uploaded_urls:
                    source = next(
                        (item for item in SS.files if item["name"] == file_name), None
                    )
                    if source is not None:
                        pdf_url = supabase_service.upload_pdf_to_storage(
                            file_name, source["data"]
                        )
                        uploaded_urls[file_name] = pdf_url
                else:
                    pdf_url = uploaded_urls[file_name]
                pdf_name = file_name

            lines = [
                InvoiceLine(
                    description=str(row.get("description") or ""),
                    amount=float(row.get("amount") or 0),
                    currency=str(row.get("currancy") or "").strip(),
                    line_order=order,
                )
                for order, row in enumerate(rows, start=1)
            ]

            parsed = SS.parsed.get(file_name) if file_name != "manual" else None
            needs_review = any(validators.validate_row(row) for row in rows)
            invoice_id = supabase_service.save_invoice(
                {
                    "client_name": _first_nonempty(rows, "Client"),
                    "invoice_no": invoice_no,
                    "invoice_date": _first_nonempty(rows, "Invo Date"),
                    "currency": _first_nonempty(rows, "currancy"),
                    "pdf_file_name": pdf_name,
                },
                pdf_url=pdf_url,
                raw_text=parsed.raw_text if parsed else "",
                extraction_method=parsed.extraction_method if parsed else "manual",
                status="needs_review" if needs_review else "saved",
            )
            supabase_service.save_invoice_lines(invoice_id, lines)

            saved_in_session.add(invoice_no)
            summary["saved"] += 1
            save_log.append(
                {
                    "File": file_name,
                    "Invo No": invoice_no,
                    "Result": "Saved",
                    "Message": f"{len(lines)} line(s) -> invoice {invoice_id[:8]}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - friendly per-invoice message
            logger.exception("Failed to save invoice %s", invoice_no)
            summary["errors"] += 1
            save_log.append(
                {
                    "File": file_name,
                    "Invo No": invoice_no,
                    "Result": "Error",
                    "Message": str(exc),
                }
            )

    st.success(
        f"Saved {summary['saved']} invoice(s), skipped {summary['duplicates']} "
        f"duplicate(s), {summary['skipped']} incomplete, {summary['errors']} error(s)."
    )
    st.dataframe(pd.DataFrame(save_log[-50:]), hide_index=True, width="stretch")


def _render_save(edited: Optional[pd.DataFrame]) -> None:
    st.header("4. Save to Supabase")

    # Only rendered when ENABLE_SUPABASE_SAVE is on (see main()).
    if not supabase_service.is_configured():
        st.warning(
            "Supabase saving is enabled but credentials are missing. "
            "Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to your secrets/.env."
        )
        st.button("Save to Supabase", disabled=True)
        return

    if edited is None or edited.empty:
        st.caption("Process PDFs first - there is nothing to save yet.")
        return

    if st.button("Save to Supabase", type="primary"):
        _run_save(edited)

    if SS.save_log:
        with st.expander("Save history (last run)"):
            st.dataframe(pd.DataFrame(SS.save_log[-50:]), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# 5. Export
# ---------------------------------------------------------------------------


def _render_export(edited: Optional[pd.DataFrame], section_no: int) -> None:
    st.header(f"{section_no}. Export to Excel")

    if edited is None or edited.empty:
        st.caption("Nothing to export yet.")
        return

    records = edited.to_dict("records")
    if validators.validate_rows(records):
        st.warning(
            "Some rows have missing values. The file will be exported as-is - "
            "please check the warnings in section 3 first."
        )

    export_name = get_export_filename()
    excel_bytes = excel_exporter.generate_excel_bytes(records)
    st.download_button(
        "Download Excel file",
        data=excel_bytes,
        file_name=export_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
    st.caption(f"Columns: {', '.join(EXPORT_COLUMNS)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("Invoice PDF to Excel Converter")

    options = _render_sidebar()

    _render_upload()

    st.header("2. Process PDFs")
    if st.button("Process PDFs", type="primary"):
        _process_all(options)
    _render_status_table()

    edited = _render_results()

    if supabase_service.is_enabled():
        # Full flow: 4. Save to Supabase, 5. Export to Excel.
        _render_save(edited)
        _render_export(edited, section_no=5)
    else:
        # Supabase disabled: hide the save section and renumber export to 4.
        _render_export(edited, section_no=4)


if __name__ == "__main__":
    main()
