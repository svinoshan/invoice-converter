"""Dashboard page: high-level summary cards, per-currency totals, status.

No uploader, no processing controls, no editable tables, no exports.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services import supabase_service
from utils import ui_helpers


def render() -> None:
    ui_helpers.init_state()
    SS = st.session_state

    st.header("Dashboard")

    summary: dict = {}
    if supabase_service.is_enabled() and supabase_service.is_configured():
        summary = ui_helpers.cached_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total saved records", summary.get("count", "-"))
    col2.metric("Needs review", summary.get("needs_review", "-"))
    col3.metric("Reviewed", summary.get("reviewed", "-"))
    col4.metric("PDFs processed", summary.get("source_files", "-"))

    st.divider()

    st.subheader("Totals by currency")
    by_currency = summary.get("by_currency", [])
    if not by_currency:
        st.caption(
            "No saved records yet - upload and process PDFs on the "
            "Upload & Process page."
        )
    else:
        chart = pd.DataFrame(by_currency)
        st.bar_chart(
            chart,
            x="currency",
            y="total_amount",
            horizontal=True,
            color="#4C9F70",
            width="stretch",
            height="content",
        )
        st.dataframe(
            chart[["currency", "count", "total_amount"]],
            column_config={
                "currency": "Currency",
                "count": "Records",
                "total_amount": st.column_config.NumberColumn(
                    "Total amount", format="%.2f"
                ),
            },
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Amounts are comparable only within a currency - totals are not "
            "summed across currencies."
        )

    st.divider()

    st.subheader("System status")
    if not supabase_service.is_enabled():
        st.caption(
            "Database saving is disabled. "
            "Processing still works, but records are only kept in the preview "
            "and Excel export from stored records is unavailable."
        )
    elif not supabase_service.is_configured():
        st.caption(
            "Database is enabled but not configured."
        )
    else:
        st.success(
            f"Connected to Database - {summary.get('count', 0)} record(s) "
            "stored in invoice_records."
        )

    last_batch = SS.last_batch_summary
    if last_batch:
        st.divider()
        st.subheader("Last processed batch")
        a, b, c, d, e = st.columns(5)
        a.metric("PDFs processed", last_batch.get("processed", 0))
        b.metric("Successfully processed", last_batch.get("success", 0))
        c.metric("Inserted or updated", last_batch.get("inserted_updated", 0))
        d.metric("Needs review", last_batch.get("needs_review", 0))
        e.metric("Failed", last_batch.get("failed", 0))

    st.divider()
    st.subheader("How it works")
    st.markdown(
        "1. **Upload & Process** - upload invoice PDFs and click Process PDFs. "
        "Valid records are upserted to Database automatically.\n"
        "2. **Review & edit** - extracted rows appear on the same page right "
        "after processing; correct anything and save.\n"
        "3. **Saved Records** - browse all persisted records with filters.\n"
        "4. **Export** - Excel is generated from the Database records.\n\n"
        "Uploaded PDF files are never stored - only the extracted invoice "
        "records (table `invoice_records`)."
    )


if __name__ == "__main__":
    render()
