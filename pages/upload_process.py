"""Upload & Process page - the complete workflow in one place.

Sections (exact order):
1. Upload PDFs
2. Process PDFs
3. Review & edit extracted rows
4. Export to Excel

The review/edit table and its Save button stay on this page - the user never
needs to switch pages to correct extracted rows. Excel export is generated
from Supabase records (a clearly labelled preview export of the current rows
is also offered).
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd
import streamlit as st

from services import (
    excel_exporter,
    processing,
    supabase_service,
    validators,
)
from services.azure_invoice_service import is_configured as azure_is_configured
from utils import ui_helpers
from utils.file_utils import get_export_filename, human_readable_bytes
from utils.logging_config import get_logger

logger = get_logger("upload_process")

EXPORT_COLUMNS = ui_helpers.EXPORT_COLUMNS


def _processing_options() -> Dict[str, bool]:
    return {
        "ocr_enabled": bool(SS.settings.get("ocr", True)),
        "azure_enabled": bool(SS.settings.get("azure", False)),
    }


SS = st.session_state


def _mark_edited() -> None:
    SS.unsaved_edits = True


# ---------------------------------------------------------------------------
# 1. Upload PDFs
# ---------------------------------------------------------------------------


def _render_upload() -> None:
    st.subheader("1. Upload PDFs")
    st.caption("Choose invoice PDF files. Drag and drop is supported.")

    uploaded = st.file_uploader(
        "Invoice PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        key="uploader",
        label_visibility="collapsed",
    )
    if uploaded is not None:
        new_files = [{"name": item.name, "data": item.getvalue()} for item in uploaded]
        if [f["name"] for f in new_files] != [f["name"] for f in SS.files]:
            SS.files = new_files
            SS.statuses = {}
            SS.preview = []
            SS.unsaved_edits = False

    if SS.files:
        total_bytes = sum(len(f["data"]) for f in SS.files)
        st.caption(
            f"{len(SS.files)} PDF(s) selected, total size "
            f"{human_readable_bytes(total_bytes)}"
        )
        with st.expander("View uploaded files"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "File name": f["name"],
                            "File size": human_readable_bytes(len(f["data"])),
                        }
                        for f in SS.files
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
    else:
        st.caption("No PDFs currently selected.")


# ---------------------------------------------------------------------------
# 2. Process PDFs
# ---------------------------------------------------------------------------


def _run_processing() -> None:
    if not SS.files:
        st.warning("Please upload at least one PDF first.")
        return

    progress = st.progress(0.0, text="Processing PDFs...")

    def report(done: int, total: int, text: str) -> None:
        progress.progress(done / total, text=text)

    try:
        result = processing.process_files(
            SS.files,
            _processing_options(),
            batch_size=SS.batch_override,
            progress_cb=report,
        )
    except Exception as exc:  # noqa: BLE001 - friendly message
        logger.exception("Batch processing failed")
        progress.empty()
        st.error(f"Processing failed: {exc}")
        return
    progress.empty()

    SS.statuses = result["statuses"]
    SS.preview = result["preview"]
    SS.save_history.extend(result["log"])
    SS.last_batch_summary = result["summary"]
    SS.unsaved_edits = False
    ui_helpers.bump_revision()

    summary = result["summary"]
    if summary["inserted_updated"]:
        st.success(
            f"Processed {summary['processed']} PDF(s) - "
            f"{summary['inserted_updated']} record(s) upserted to Database. "
            f"{summary['needs_review']} need review, {summary['failed']} failed."
        )
    elif summary["processed"]:
        st.warning(
            f"Processed {summary['processed']} PDF(s) - "
            f"{summary['needs_review']} need review, {summary['failed']} failed. "
            "Valid records were upserted."
        )
    else:
        st.warning("No PDFs were processed.")


def _render_process() -> None:
    st.subheader("2. Process PDFs")

    if st.button("Process PDFs", type="primary"):
        _run_processing()

    # st.caption("Large uploads are processed automatically in safe chunks.")

    summary = SS.last_batch_summary
    if summary:
        current_names = sorted(f["name"] for f in SS.files)
        batch_names = sorted(summary.get("files") or [])
        if batch_names and batch_names != current_names:
            st.caption(
                "Last processed batch - the uploaded files have changed "
                "since it ran."
            )
        else:
            st.caption("Last processed batch.")
        a, b, c, d, e = st.columns(5)
        a.metric("PDFs processed", summary.get("processed", 0))
        b.metric("Successfully processed", summary.get("success", 0))
        c.metric("Inserted or updated", summary.get("inserted_updated", 0))
        d.metric("Needs review", summary.get("needs_review", 0))
        e.metric("Failed", summary.get("failed", 0))

    if SS.statuses:
        with st.expander("View processing details"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "File": file_item["name"],
                            "Invoice No": info.get("invoice_no", ""),
                            "Status": info["status"],
                            "Message": info["message"],
                        }
                        for file_item in SS.files
                        if file_item["name"] in SS.statuses
                        for info in [SS.statuses[file_item["name"]]]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


# ---------------------------------------------------------------------------
# 3. Review & edit extracted rows
# ---------------------------------------------------------------------------


def _save_edited_rows(records: List[Dict]) -> None:
    """Upsert every editor row that has an invoice number."""
    if not supabase_service.is_enabled():
        st.error(
            "Database saving is disabled. "
        )
        return
    if not supabase_service.is_configured():
        st.error(
            "Database is not configured. "
        )
        return

    meta_by_invoice = {
        str(row.get("Invoice No") or ""): row
        for row in SS.preview
        if row.get("Invoice No")
    }

    to_save: List[Dict] = []
    skipped = 0
    log: List[Dict] = []
    for index, row in enumerate(records):
        invoice_no = str(row.get("Invoice No") or "").strip()
        if not invoice_no:
            skipped += 1
            log.append(
                {
                    "File": "-",
                    "Invoice No": "",
                    "Result": "Skipped",
                    "Message": "Invoice number is empty - not upserted",
                }
            )
            continue
        # The data editor preserves row order, so a corrected invoice number
        # still lines up positionally with its original preview row and keeps
        # Source file / Raw text. Fall back to the invoice-number lookup when
        # the positional row is a different, still-existing invoice (e.g. a
        # row was deleted above this one).
        prior = SS.preview[index] if index < len(SS.preview) else {}
        if (
            str(prior.get("Invoice No") or "") != invoice_no
            and invoice_no in meta_by_invoice
        ):
            prior = meta_by_invoice[invoice_no]
        record = ui_helpers.row_to_record(
            row,
            source_file_name=str(prior.get("Source file") or ""),
            raw_text=str(prior.get("Raw text") or ""),
        )
        record["extraction_status"] = (
            "processed" if not validators.needs_review(row) else "needs_review"
        )
        to_save.append(record)

    saved = 0
    if to_save:
        saved = supabase_service.upsert_invoice_records(to_save)
    for record in to_save:
        log.append(
            {
                "File": record.get("source_file_name") or "-",
                "Invoice No": record.get("invoice_no"),
                "Result": "Saved",
                "Message": "Upserted (insert or update)",
            }
        )
    SS.save_history.extend(log)

    merged_preview: List[Dict] = []
    for index, row in enumerate(records):
        invoice_no = str(row.get("Invoice No") or "").strip()
        prior = SS.preview[index] if index < len(SS.preview) else {}
        if (
            str(prior.get("Invoice No") or "") != invoice_no
            and invoice_no in meta_by_invoice
        ):
            prior = meta_by_invoice[invoice_no]
        merged = dict(prior)
        merged.update({column: row.get(column) for column in EXPORT_COLUMNS})
        merged_preview.append(merged)
    SS.preview = merged_preview
    SS.unsaved_edits = False
    ui_helpers.bump_revision()

    st.success(
        f"Upserted {saved} record(s) on invoice_no; {skipped} row(s) skipped "
        "because the invoice number is missing."
    )


def _render_review() -> None:
    if not SS.preview:
        st.subheader("3. Review & edit extracted rows")
        st.caption("Process PDFs first. Extracted rows will appear here.")
        return

    # Pending = explicit editor edits OR rows that were never upserted
    # (only valid records are auto-saved after processing). Needs-review
    # rows must keep the Save button enabled without any manual edit.
    pending = SS.unsaved_edits or any(
        validators.needs_review(row) for row in SS.preview
    )

    header_left, header_right = st.columns([3, 1], vertical_alignment="center")
    with header_left:
        st.subheader("3. Review & edit extracted rows")
    with header_right:
        save_clicked = st.button(
            "Save changes",
            type="primary",
            width="stretch",
            disabled=not pending,
        )

    st.caption(
        ":red[Unsaved changes]" if pending else ":green[All changes saved]"
    )
    st.caption(
        "Valid records are saved automatically after processing. Use Save "
        "changes only after manual edits."
    )

    frame = pd.DataFrame(SS.preview, columns=EXPORT_COLUMNS + ["Source file", "Raw text"])
    edited = st.data_editor(
        frame[EXPORT_COLUMNS],
        key="upload_editor",
        num_rows="dynamic",
        width="stretch",
        on_change=_mark_edited,
        column_config={
            "Invoice Date": st.column_config.TextColumn("Invoice Date"),
            "Description": st.column_config.TextColumn(
                "Description", width="large"
            ),
            "Sub total": st.column_config.NumberColumn(
                "Sub total", format="%.2f", width="small"
            ),
            "Total amount": st.column_config.NumberColumn(
                "Total amount", format="%.2f", width="small"
            ),
            "Currency": st.column_config.TextColumn("Currency", width="small"),
        },
    )

    records = edited.to_dict("records")

    if save_clicked:
        _save_edited_rows(records)

    validation = validators.validate_rows(records)
    if validation:
        st.warning(
            f"{len(validation)} row(s) have issues - check the values below. "
            "Only a missing invoice number or total amount blocks saving; "
            "other issues (e.g. a missing invoice date) are advisory."
        )
        for item in validation[:25]:
            st.markdown(f"- **Row {item['row']}**: {', '.join(item['issues'])}")
    else:
        st.success("All rows look complete.")

    with st.expander("View full Description text"):
        if records:
            labels = [
                f"{row.get('Invoice No') or '(no invoice no)'} - "
                f"{row.get('Client') or '(no client)'}"
                for row in records
            ]
            selected = st.selectbox(
                "Select a row to preview its full Description",
                labels,
                key="desc_preview",
            )
            if selected is not None:
                st.text_area(
                    "Full description",
                    value=records[labels.index(selected)].get("Description") or "",
                    height=160,
                    disabled=True,
                    label_visibility="collapsed",
                )

    with st.expander("Debug extracted text"):
        for row in SS.preview:
            st.markdown(f"**{row.get('Source file') or 'manual'}**")
            st.code((row.get("Raw text") or "")[:20000])


# ---------------------------------------------------------------------------
# 4. Export to Excel
# ---------------------------------------------------------------------------


def _render_export() -> None:
    st.subheader("4. Export to Excel")

    pending = SS.unsaved_edits or any(
        validators.needs_review(row) for row in SS.preview
    )
    if pending:
        st.warning(
            "You have unsaved edits. Save edited records before exporting "
            "all saved records."
        )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**All saved records**")
        if not supabase_service.is_enabled():
            st.warning(
                "Excel export is generated from Database records - "
                "enable Database saving in Settings first."
            )
            st.download_button(
                "Export all records to Excel",
                data=b"",
                file_name=get_export_filename(),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disabled=True,
            )
        else:
            try:
                bytes_ = ui_helpers.cached_export_bytes_from_db()
            except Exception as exc:
                st.error(f"Could not load records from Database: {exc}")
            else:
                count = ui_helpers.cached_summary().get("count", 0)
                st.download_button(
                    "Export all records to Excel",
                    data=bytes_,
                    file_name=get_export_filename(),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                st.caption(
                    f"{count} record(s) from Database."
                    if count
                    else "No saved records in Database yet."
                )

    with col2:
        st.markdown("**Current extracted rows (preview)**")
        if not SS.preview:
            st.download_button(
                "Export current extracted rows to Excel (preview)",
                data=b"",
                file_name=get_export_filename(),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disabled=True,
            )
            st.caption("Process PDFs first - there are no extracted rows yet.")
        else:
            st.download_button(
                "Export current extracted rows to Excel (preview)",
                data=excel_exporter.generate_excel_bytes(SS.preview),
                file_name=get_export_filename(),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.caption(
                "Preview only - generated from the current extracted rows, "
                "not from Database records."
            )

    st.caption(f"Final columns: {', '.join(EXPORT_COLUMNS)}")


def render() -> None:
    ui_helpers.init_state()
    st.header("Upload & Process")

    if not supabase_service.is_enabled():
        st.info(
            "Database saving is disabled - records stay in the preview below "
            "and are not persisted. Enable it in Settings to upsert and "
            "export from stored records."
        )

    _render_upload()
    _render_process()
    _render_review()
    _render_export()


if __name__ == "__main__":
    render()
