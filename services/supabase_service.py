"""Supabase PostgreSQL + Storage integration.

Uses the service role key server-side only. The service role key is never
exposed to the browser; it is read from Streamlit secrets or environment
variables.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from models.schemas import ParsedInvoice
from utils.config import (
    ENABLE_SUPABASE_SAVE,
    SUPABASE_BUCKET,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)
from utils.file_utils import unique_filename
from utils.logging_config import get_logger

logger = get_logger("supabase_service")


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when Supabase credentials are missing."""


_TABLE_MISSING_HINT = (
    "Supabase tables are missing. Open the Supabase Dashboard > SQL Editor, "
    "run the full contents of sql/schema.sql, then reload the app."
)


def _is_missing_table(exc: Exception) -> bool:
    """Detect PGRST205 (table not in PostgREST schema cache)."""
    text = str(exc)
    return "PGRST205" in text or "Could not find the table" in text


def _friendly_error(exc: Exception) -> RuntimeError:
    """Wrap an exception with an actionable hint when tables are missing."""
    if _is_missing_table(exc):
        return RuntimeError(_TABLE_MISSING_HINT)
    return RuntimeError(str(exc))


_client: Any = None


def is_enabled() -> bool:
    """Return True when the Supabase save feature is switched on in config."""
    return ENABLE_SUPABASE_SAVE


def is_configured() -> bool:
    """Return True when Supabase URL and service role key are available."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _get_client() -> Any:
    """Create (once) and return the Supabase client."""
    global _client

    if not is_enabled():
        raise SupabaseNotConfiguredError(
            "Supabase saving is disabled. Set ENABLE_SUPABASE_SAVE=true "
            "to turn it on."
        )
    if not is_configured():
        raise SupabaseNotConfiguredError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured."
        )
    if _client is None:
        try:
            from supabase import create_client
        except ImportError as exc:
            raise RuntimeError("The 'supabase' package is not installed") from exc
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


def upload_pdf_to_storage(
    file_name: str, file_bytes: bytes, bucket: Optional[str] = None
) -> str:
    """Upload ``file_bytes`` to the storage bucket and return its public URL."""
    client = _get_client()
    bucket = bucket or SUPABASE_BUCKET or "invoice-pdfs"
    storage_path = f"uploads/{unique_filename(file_name)}"

    try:
        client.storage.from_(bucket).upload(
            storage_path,
            file_bytes,
            file_options={"content-type": "application/pdf", "cache-control": "3600"},
        )
    except TypeError:
        # Older supabase-py versions accept options positionally.
        client.storage.from_(bucket).upload(
            storage_path, file_bytes, {"content-type": "application/pdf"}
        )
    except Exception as exc:
        logger.error("Storage upload failed for %s: %s", file_name, exc)
        raise RuntimeError(f"Could not upload PDF to storage: {exc}") from exc

    return f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{bucket}/{storage_path}"


def check_duplicate_invoice(invoice_no: str) -> bool:
    """Return True when an invoice with this number already exists."""
    if not invoice_no:
        return False
    try:
        response = (
            _get_client()
            .table("invoices")
            .select("id")
            .eq("invoice_no", invoice_no)
            .maybe_single()
            .execute()
        )
        return response.data is not None
    except Exception as exc:
        logger.warning("Duplicate check failed for %s: %s", invoice_no, exc)
        return False


def get_existing_invoices(limit: int = 200) -> List[str]:
    """Return the most recent invoice numbers stored in the database."""
    try:
        response = (
            _get_client()
            .table("invoices")
            .select("invoice_no")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [row["invoice_no"] for row in (response.data or [])]
    except Exception as exc:
        logger.error("get_existing_invoices failed: %s - %s", _friendly_error(exc), exc)
        return []


def save_invoice(
    invoice_data: Dict[str, Any],
    pdf_url: str,
    raw_text: str,
    extraction_method: str,
    status: str,
) -> str:
    """Insert one invoice row and return the new row id.

    ``invoice_data`` should contain ``client_name``, ``invoice_no``,
    ``invoice_date`` (canonical ``d-mmm-yy`` string), ``currency``,
    ``description_text``, ``total_amount`` and ``pdf_file_name``. An
    unparseable date is stored as NULL rather than failing the insert.
    """
    invoice_date = invoice_data.get("invoice_date")
    iso_date: Optional[str] = None
    if invoice_date:
        try:
            iso_date = datetime.strptime(str(invoice_date), "%d-%b-%y").date().isoformat()
        except ValueError:
            iso_date = None

    total_amount = invoice_data.get("total_amount")
    try:
        total_amount = round(float(total_amount), 2)
    except (TypeError, ValueError):
        total_amount = None

    row = {
        "client_name": (invoice_data.get("client_name") or "").strip(),
        "invoice_no": (invoice_data.get("invoice_no") or "").strip(),
        "invoice_date": iso_date,
        "currency": (invoice_data.get("currency") or "").strip(),
        "description_text": (invoice_data.get("description_text") or "").strip(),
        "total_amount": total_amount,
        "pdf_file_name": (invoice_data.get("pdf_file_name") or "").strip(),
        "pdf_file_url": pdf_url or "",
        "raw_text": raw_text or "",
        "extraction_method": extraction_method or "",
        "status": status or "saved",
    }

    try:
        response = _get_client().table("invoices").insert(row).execute()
    except Exception as exc:
        logger.error("save_invoice failed: %s", exc)
        raise _friendly_error(exc) from exc

    return response.data[0]["id"]


def save_invoice_from_parsed(invoice: ParsedInvoice, pdf_url: str) -> str:
    """Insert one parsed invoice row and return the new row id."""
    return save_invoice(
        invoice_data={
            "client_name": invoice.client_name,
            "invoice_no": invoice.invoice_no,
            "invoice_date": invoice.invoice_date,
            "currency": invoice.currency,
            "description_text": invoice.description_text,
            "total_amount": invoice.total_amount,
            "pdf_file_name": "",
        },
        pdf_url=pdf_url,
        raw_text=invoice.raw_text,
        extraction_method=invoice.extraction_method,
        status="saved",
    )
