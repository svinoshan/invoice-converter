"""Saved Records page: browse, filter, edit and export persisted records.

Always loads from Supabase (with a session-level one-time auto-load), never
relies only on session state. Inline edits can be saved back with an upsert
on invoice_no, and the currently filtered set can be exported to Excel.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Dict, List

import pandas as pd
import streamlit as st

from services import excel_exporter, supabase_service, validators
from utils import ui_helpers
from utils.file_utils import get_export_filename


def _apply_date_range(records: List[Dict], start, end) -> List[Dict]:
    if not records:
        return records
    if start is None and end is None:
        return records
    filtered = []
    for rec in records:
        iso = rec.get("invoice_date")
        if isinstance(iso, date_type):
            iso = iso.isoformat()
        if not iso:
            continue
        if start is not None and iso < start.isoformat():
            continue
        if end is not None and iso > end.isoformat():
            continue
        filtered.append(rec)
    return filtered


def render() -> None:
    ui_helpers.init_state()
    SS = st.session_state

    st.header("Saved Records")

    if not supabase_service.is_enabled():
        st.caption(
            "Database saving is disabled - no persisted records to show. "
            "Enable it in Settings."
        )
        return

    col1, col2, col3, col4 = st.columns(4)
    status_filter = col1.selectbox(
        "Status", options=["All", "processed", "needs_review"], key="sr_status"
    )
    reviewed_filter = col2.selectbox(
        "Reviewed", options=["All", "Yes", "No"], key="sr_reviewed"
    )
    client_filter = col3.text_input("Client contains", key="sr_client")
    invoice_no_filter = col4.text_input("Invoice No contains", key="sr_invoice_no")

    date_col1, date_col2 = st.columns(2)
    start_date = date_col1.date_input(
        "Invoice date from", value=None, key="sr_date_from"
    )
    end_date = date_col2.date_input(
        "Invoice date to", value=None, key="sr_date_to"
    )

    load_col, info_col = st.columns([1, 3])
    if load_col.button("Load saved records", type="primary"):
        reviewed = None
        if reviewed_filter == "Yes":
            reviewed = True
        elif reviewed_filter == "No":
            reviewed = False
        SS.saved_records = supabase_service.get_invoice_records(
            status=None if status_filter == "All" else status_filter,
            reviewed=reviewed,
            client=client_filter,
            invoice_no=invoice_no_filter,
        )
        SS.saved_records = _apply_date_range(
            SS.saved_records, start_date or None, end_date or None
        )
        SS.saved_loaded = True
        info_col.caption(
            f"Loaded {len(SS.saved_records)} record(s) from Database with the "
            "current filters."
        )
    elif not SS.saved_loaded:
        SS.saved_records = supabase_service.get_all_invoice_records()
        SS.saved_loaded = True
        info_col.caption(
            f"Auto-loaded {len(SS.saved_records)} record(s) from Database."
        )

    if not SS.saved_records:
        st.caption("No records match the current filters.")
        return

    frame = pd.DataFrame([ui_helpers.record_to_row(r) for r in SS.saved_records])
    frame["Reviewed"] = [bool(r.get("reviewed")) for r in SS.saved_records]

    edited = st.data_editor(
        frame,
        key="saved_editor",
        width="stretch",
        column_config={
            "Invoice Date": st.column_config.TextColumn("Invoice Date"),
            "Sub total": st.column_config.NumberColumn("Sub total", format="%.2f"),
            "Total amount": st.column_config.NumberColumn("Total amount", format="%.2f"),
            "Reviewed": st.column_config.CheckboxColumn("Reviewed"),
        },
    )

    records = edited.to_dict("records")
    validation = validators.validate_rows(records)
    if validation:
        st.warning(
            f"{len(validation)} row(s) have issues - check the values below. "
            "Only a missing invoice number or total amount blocks saving; "
            "other issues (e.g. a missing invoice date) are advisory."
        )
        for item in validation[:25]:
            st.markdown(f"- **Row {item['row']}**: {', '.join(item['issues'])}")

    col_a, col_b = st.columns(2)
    if col_a.button("Save changes"):
        _save_changes(records)
    if col_b.button("Export current filtered records to Excel"):
        st.download_button(
            "Download filtered Excel file",
            data=excel_exporter.generate_excel_bytes(records),
            file_name=get_export_filename(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption(f"Exported {len(records)} filtered record(s).")


def _save_changes(records: List[Dict]) -> None:
    SS = st.session_state
    to_save: List[Dict] = []
    skipped = 0
    for row in records:
        invoice_no = str(row.get("Invoice No") or "").strip()
        if not invoice_no:
            skipped += 1
            continue
        record = ui_helpers.row_to_record(
            row, reviewed=bool(row.get("Reviewed"))
        )
        record["extraction_status"] = (
            "processed" if not validators.needs_review(row) else "needs_review"
        )
        to_save.append(record)

    saved = 0
    if to_save:
        saved = supabase_service.upsert_invoice_records(to_save)
    ui_helpers.bump_revision()
    st.success(
        f"Upserted {saved} record(s); {skipped} row(s) skipped (missing invoice number)."
    )


if __name__ == "__main__":
    render()
