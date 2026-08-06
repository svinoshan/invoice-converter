"""Invoice PDF to Excel Converter - entry point.

Sidebar navigation between four pages (st.navigation / st.Page):

1. Dashboard        - high-level summary cards and system status only
2. Upload & Process - the main workflow page: upload, process, review/edit,
                      save and export in one flow (no separate Review & Edit
                      page - extracted rows always appear right after
                      processing on this page)
3. Saved Records    - all persisted Supabase records with filters
4. Settings         - processing options and advanced/debug settings

Supabase remains the source of truth: valid records are upserted after
processing and Excel is generated from the stored records.
"""

from __future__ import annotations

import streamlit as st

from utils import ui_helpers

st.set_page_config(
    page_title="Invoice PDF to Excel Converter",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

ui_helpers.init_state()

# File-based pages: the URL pathname is inferred from each unique filename,
# so pages/dashboard.py -> "/dashboard", pages/upload_process.py ->
# "/upload_process", etc.
pg = st.navigation(
    [
        st.Page("pages/dashboard.py", title="Dashboard", default=True),
        st.Page("pages/upload_process.py", title="Upload & Process"),
        st.Page("pages/saved_records.py", title="Saved Records"),
        st.Page("pages/settings.py", title="Settings"),
    ],
    position="sidebar",
)

with st.sidebar:
    st.markdown("### Invoice PDF to Excel Converter")
    st.caption(
        "Upload one or more invoice PDFs. The app extracts the client, "
        "invoice number, date, currency and line items, then lets you "
        "review, fix, save and export to Excel."
    )

pg.run()
