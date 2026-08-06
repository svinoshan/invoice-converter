"""Settings page: processing options, session controls and debug info.

Nothing here touches Supabase records - only how this browser session
processes PDFs (OCR, Azure, internal batch override) plus helpers such as
clearing the workflow state or downloading the Excel template.
"""

from __future__ import annotations

import streamlit as st

from services import azure_invoice_service, excel_exporter, supabase_service
from utils import ui_helpers
from utils import config
from utils.file_utils import get_export_filename


def render() -> None:
    ui_helpers.init_state()
    SS = st.session_state

    st.header("Settings")

    st.subheader("Processing options")
    ocr_enabled = st.checkbox(
        "Fall back to OCR when no embedded text is found",
        value=bool(SS.settings.get("ocr", True)),
        key="set_ocr",
    )
    azure_enabled = st.checkbox(
        "Use Azure Document Intelligence when configured",
        value=bool(SS.settings.get("azure", False)) or azure_invoice_service.is_configured(),
        key="set_azure",
    )
    SS.settings = {"ocr": ocr_enabled, "azure": azure_enabled}

    azure_status = (
        "Configured - invoices with embedded text still use the fast local "
        "parser, Azure is used when needed."
        if azure_invoice_service.is_configured()
        else "Not configured "
    )
    st.caption(f"Azure status: {azure_status}")

    st.divider()

    st.subheader("Database status")
    if not supabase_service.is_enabled():
        st.caption(
            "Database saving is disabled."
        )
    elif not supabase_service.is_configured():
        st.caption(
            "Database is enabled but not configured."
        )
    else:
        st.success("Database is enabled and configured.")

    st.divider()

    st.subheader("Session")
    if st.button("Clear session data", use_container_width=True):
        ui_helpers.clear_workflow_state()
        ui_helpers.bump_revision()
        st.rerun()

    st.download_button(
        "Download empty Excel template",
        data=excel_exporter.generate_template_bytes(),
        file_name=get_export_filename("invoice_template"),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.divider()

    with st.expander("Advanced / debug"):
        st.caption(
            "Override the automatic internal chunk size. 0 means the app "
            "picks a safe batch automatically."
        )
        override = st.number_input(
            "Internal batch size override (0 = automatic)",
            min_value=0,
            max_value=200,
            value=int(SS.batch_override or 0),
            step=1,
            key="set_batch_override",
        )
        SS.batch_override = override

        st.caption("Current configuration:")
        st.json(
            {
                "supabase_enabled": config.ENABLE_SUPABASE_SAVE,
                "supabase_configured": supabase_service.is_configured(),
                "supabase_url_set": bool(config.SUPABASE_URL),
                "azure_configured": azure_invoice_service.is_configured(),
            }
        )
        st.caption(
            "Session records in this browser tab - not the persisted "
            "Supabase table. Clear session data to reset them."
        )


if __name__ == "__main__":
    render()
