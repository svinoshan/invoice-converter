"""Shared helpers used across the multipage UI.

Keeps session-state defaults, Supabase-summary caching, record <-> row
conversions and Excel-generation helpers in one place so page modules stay
thin.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from services import excel_exporter, supabase_service
from utils.file_utils import get_export_filename, human_readable_bytes  # noqa: F401

EXPORT_COLUMNS = list(excel_exporter.EXPORT_COLUMNS)

SS = st.session_state

CLEARABLE_KEYS = (
    "files",
    "statuses",
    "preview",
    "save_history",
    "saved_records",
    "saved_loaded",
    "last_batch_summary",
    "unsaved_edits",
)


def init_state() -> None:
    SS.setdefault("files", [])            # [{"name": str, "data": bytes}]
    SS.setdefault("statuses", {})         # name -> {"status", "message", "method", "invoice_no"}
    SS.setdefault("preview", [])          # editor rows from the current upload
    SS.setdefault("save_history", [])     # per-batch upsert log
    SS.setdefault("saved_records", [])    # records loaded from Supabase
    SS.setdefault("saved_loaded", False)  # whether Saved Records was loaded once
    SS.setdefault("revision", 0)          # bumped after writes to refresh caches
    SS.setdefault("last_batch_summary", None)
    SS.setdefault("unsaved_edits", False)
    SS.setdefault("batch_override", 0)    # 0 = automatic chunking
    SS.setdefault("settings", {"ocr": True, "azure": False})


@st.cache_data(ttl=10, show_spinner=False)
def cached_summary() -> Dict[str, Any]:
    """Supabase totals: count + sums. Cached 10s; cleared on writes."""
    return supabase_service.get_invoice_summary_totals()


@st.cache_data(ttl=10, show_spinner=False)
def cached_export_bytes_from_db() -> bytes:
    """Excel workbook of every stored record. Cached 10s; cleared on writes."""
    records = supabase_service.get_all_invoice_records()
    return export_bytes_from_records(records)


def bump_revision() -> None:
    SS.revision = SS.revision + 1
    cached_summary.clear()
    cached_export_bytes_from_db.clear()


def clear_workflow_state() -> None:
    """Reset everything except Settings / revision."""
    for key in CLEARABLE_KEYS:
        SS.pop(key, None)


# ---------------------------------------------------------------------------
# Conversions: Supabase record <-> editor row
# ---------------------------------------------------------------------------


def _to_float(value: object) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def record_to_row(rec: Dict) -> Dict[str, object]:
    """Map a Supabase record (clean field names) to an editor/export row."""
    invoice_date = rec.get("invoice_date")
    if isinstance(invoice_date, date_type):
        invoice_date = invoice_date.strftime("%d-%b-%y").lstrip("0")
    return {
        "Client": rec.get("client") or "",
        "Invoice No": rec.get("invoice_no") or "",
        "Invoice Date": invoice_date or "",
        "Description": rec.get("description") or "",
        "Sub total": rec.get("sub_total"),
        "Total amount": rec.get("total_amount"),
        "Currency": rec.get("currency") or "",
    }


def row_to_record(
    row: Dict,
    source_file_name: str = "",
    raw_text: str = "",
    reviewed: Optional[bool] = None,
) -> Dict[str, object]:
    """Map an editor row back to the clean Supabase record fields.

    ``reviewed`` is only included when explicitly set. Omitting it lets the
    upsert preserve the existing human-verification flag instead of resetting
    it when a PDF is re-processed.
    """
    record = {
        "client": str(row.get("Client") or "").strip(),
        "invoice_no": str(row.get("Invoice No") or "").strip(),
        "invoice_date": str(row.get("Invoice Date") or "").strip(),
        "description": str(row.get("Description") or "").strip(),
        "sub_total": _to_float(row.get("Sub total")),
        "total_amount": _to_float(row.get("Total amount")),
        "currency": str(row.get("Currency") or "").strip(),
        "source_file_name": source_file_name,
        "raw_text": raw_text,
        "extraction_status": "processed",
    }
    if reviewed is not None:
        record["reviewed"] = reviewed
    return record


def export_bytes_from_records(records: List[Dict]) -> bytes:
    """Build an Excel workbook from Supabase-style records (7 columns)."""
    rows = [record_to_row(rec) for rec in records]
    return excel_exporter.generate_excel_bytes(rows)
