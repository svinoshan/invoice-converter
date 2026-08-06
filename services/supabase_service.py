"""Supabase PostgreSQL integration: the source of truth for invoice records.

Extracted invoices are upserted into the ``invoice_records`` table using
``invoice_no`` as the unique conflict key, so re-processing a PDF updates the
existing row instead of duplicating it. The table (see ``sql/schema.sql``) is:

    client, invoice_no (unique, not null), invoice_date, description,
    sub_total, total_amount, currency, source_file_name, raw_text,
    extraction_status, reviewed, created_at, updated_at

Uploaded PDF files are intentionally NOT stored - only the extracted records.
Excel exports are generated from these records, never from session state.

Uses the service role key server-side only; it is never exposed to the
browser and is read from Streamlit secrets or environment variables.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.config import ENABLE_SUPABASE_SAVE, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from utils.logging_config import get_logger

logger = get_logger("supabase_service")

_INVOICE_RECORDS_TABLE = "invoice_records"


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


# ---------------------------------------------------------------------------
# Record normalisation
# ---------------------------------------------------------------------------


def _to_iso_date(value: object) -> Optional[str]:
    """Convert the canonical ``d-mmm-yy`` string to ISO (for a date column)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


def _to_optional_float(value: object) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return round(float(text.replace(",", "")), 2)
    except ValueError:
        return None


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Map a record (clean field names) to the database row payload.

    ``reviewed``, ``raw_text`` and ``source_file_name`` are included only
    when the caller actually provides them (non-empty), so automatic or
    partial upserts never overwrite the stored metadata - e.g. saving a
    Reviewed checkbox on the Saved Records page must not wipe the original
    extraction text or source PDF name.
    """
    payload = {
        "client": (record.get("client") or "").strip(),
        "invoice_no": (record.get("invoice_no") or "").strip(),
        "invoice_date": _to_iso_date(record.get("invoice_date")),
        "description": (record.get("description") or "").strip(),
        "sub_total": _to_optional_float(record.get("sub_total")),
        "total_amount": _to_optional_float(record.get("total_amount")),
        "currency": (record.get("currency") or "").strip(),
        "extraction_status": (record.get("extraction_status") or "processed").strip(),
        "updated_at": "now",
    }
    if record.get("reviewed") is not None:
        payload["reviewed"] = bool(record.get("reviewed"))
    if record.get("raw_text"):
        payload["raw_text"] = (record.get("raw_text") or "").strip()
    if record.get("source_file_name"):
        payload["source_file_name"] = (record.get("source_file_name") or "").strip()
    return payload


# ---------------------------------------------------------------------------
# Upsert / read / query
# ---------------------------------------------------------------------------


def upsert_invoice_records(records: List[Dict[str, Any]]) -> int:
    """Upsert several invoices on ``invoice_no``; return the number upserted.

    Records without an ``invoice_no`` are skipped so a missing number never
    creates a broken or duplicate row.
    """
    payload = [_normalize_record(r) for r in records if (r.get("invoice_no") or "").strip()]
    if not payload:
        return 0
    try:
        _get_client().table(_INVOICE_RECORDS_TABLE).upsert(
            payload, on_conflict="invoice_no"
        ).execute()
    except Exception as exc:
        logger.error("upsert_invoice_records failed: %s", exc)
        raise _friendly_error(exc) from exc
    return len(payload)


def upsert_invoice_record(record: Dict[str, Any]) -> int:
    """Upsert a single invoice on ``invoice_no``; return 1 when written."""
    return upsert_invoice_records([record])


def check_invoice_exists(invoice_no: str) -> bool:
    """Return True when an invoice with this number is already stored."""
    invoice_no = (invoice_no or "").strip()
    if not invoice_no:
        return False
    try:
        response = (
            _get_client()
            .table(_INVOICE_RECORDS_TABLE)
            .select("invoice_no")
            .eq("invoice_no", invoice_no)
            .maybe_single()
            .execute()
        )
        return response.data is not None
    except Exception as exc:
        logger.warning("check_invoice_exists failed for %s: %s", invoice_no, exc)
        return False


def update_reviewed_status(invoice_no: str, reviewed: bool) -> bool:
    """Set the ``reviewed`` flag for a single invoice number."""
    try:
        response = (
            _get_client()
            .table(_INVOICE_RECORDS_TABLE)
            .update({"reviewed": bool(reviewed), "updated_at": "now"})
            .eq("invoice_no", (invoice_no or "").strip())
            .execute()
        )
        return bool(response.data)
    except Exception as exc:
        logger.error("update_reviewed_status failed for %s: %s", invoice_no, exc)
        raise _friendly_error(exc) from exc


def get_all_invoice_records(
    limit: int = 2000, order: str = "invoice_date"
) -> List[Dict[str, Any]]:
    """Return stored invoice records (newest invoice dates first)."""
    return get_invoice_records(limit=limit, order=order)


def get_invoice_records(
    limit: int = 2000,
    order: str = "invoice_date",
    status: Optional[str] = None,
    reviewed: Optional[bool] = None,
    client: str = "",
    invoice_no: str = "",
    invoice_date: str = "",
) -> List[Dict[str, Any]]:
    """Return stored invoice records with optional filters applied."""
    try:
        query = _get_client().table(_INVOICE_RECORDS_TABLE).select("*")
        if status:
            query = query.eq("extraction_status", status)
        if reviewed is not None:
            query = query.eq("reviewed", reviewed)
        if client:
            query = query.ilike("client", f"%{client}%")
        if invoice_no:
            query = query.ilike("invoice_no", f"%{invoice_no}%")
        if invoice_date:
            iso = _to_iso_date(invoice_date)
            if iso:
                query = query.eq("invoice_date", iso)
        response = query.order(order, desc=True).limit(limit).execute()
        return list(response.data or [])
    except Exception as exc:
        logger.error("get_invoice_records failed: %s - %s", _friendly_error(exc), exc)
        return []


def get_invoice_records_by_status(status: str) -> List[Dict[str, Any]]:
    """Return stored records filtered by ``extraction_status``."""
    return get_invoice_records(status=(status or "").strip() or None)


def get_invoice_record_count() -> int:
    """Return the total number of stored invoice records."""
    try:
        response = (
            _get_client()
            .table(_INVOICE_RECORDS_TABLE)
            .select("id", count="exact")
            .limit(0)
            .execute()
        )
        if response.count is not None:
            return int(response.count)
        return 0
    except Exception as exc:
        logger.error("get_invoice_record_count failed: %s - %s", _friendly_error(exc), exc)
        return 0


def get_invoice_summary_totals() -> Dict[str, Any]:
    """Return lightweight Dashboard aggregates for the stored records.

    Only the aggregation-relevant columns are fetched - never the raw_text
    blobs. Supabase Edge/REST does not expose aggregates without SQL RPCs,
    so counts and per-currency totals are computed in Python.

    Returns:
        count: total records
        needs_review: records needing validation review
        reviewed: records manually verified by a human
        source_files: distinct source PDF names
        by_currency: [{currency, count, total_amount}] sorted by total desc
    """
    try:
        response = (
            _get_client()
            .table(_INVOICE_RECORDS_TABLE)
            .select("sub_total, total_amount, currency, extraction_status, reviewed, source_file_name")
            .limit(10000)
            .execute()
        )
        records = list(response.data or [])
    except Exception as exc:
        logger.error("get_invoice_summary_totals failed: %s", exc)
        return {
            "count": 0,
            "needs_review": 0,
            "reviewed": 0,
            "source_files": 0,
            "by_currency": [],
        }
    per_currency: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        currency = (rec.get("currency") or "Unknown").strip() or "Unknown"
        bucket = per_currency.setdefault(currency, {"count": 0, "total_amount": 0.0})
        bucket["count"] += 1
        bucket["total_amount"] += float(rec.get("total_amount") or 0)
    by_currency = [
        {"currency": cur, "count": b["count"], "total_amount": round(b["total_amount"], 2)}
        for cur, b in sorted(
            per_currency.items(), key=lambda item: item[1]["total_amount"], reverse=True
        )
    ]
    return {
        "count": len(records),
        "needs_review": sum(
            1 for r in records if (r.get("extraction_status") or "") == "needs_review"
        ),
        "reviewed": sum(1 for r in records if r.get("reviewed")),
        "source_files": len({r.get("source_file_name") for r in records if r.get("source_file_name")}),
        "by_currency": by_currency,
    }