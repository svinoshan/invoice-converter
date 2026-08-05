"""Optional Azure Document Intelligence integration.

Uses the prebuilt-invoice model to extract structured invoice fields. Only
active when ``AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`` and
``AZURE_DOCUMENT_INTELLIGENCE_KEY`` are configured.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from utils.config import (
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
    AZURE_DOCUMENT_INTELLIGENCE_KEY,
)
from utils.logging_config import get_logger

logger = get_logger("azure_invoice_service")


def is_configured() -> bool:
    """Return True when Azure credentials are available."""
    return bool(AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY)


def _safe_content(field: Any) -> str:
    if not field:
        return ""
    return str(field.get("content") or field.get("valueString") or "").strip()


def analyze_invoice(file_bytes: bytes) -> Dict[str, Any]:
    """Run Azure prebuilt-invoice analysis over ``file_bytes``.

    Returns a dict with keys ``success``, ``error``, ``raw_text``,
    ``client_name``, ``invoice_no``, ``invoice_date``, ``currency``,
    ``total_amount`` and ``lines`` (list of
    ``{"description", "amount", "currency"}``).
    """
    if not is_configured():
        return {"success": False, "error": "Azure Document Intelligence is not configured"}

    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError as exc:
        logger.warning("azure-ai-documentintelligence is not installed: %s", exc)
        return {"success": False, "error": "Azure SDK is not installed"}

    try:
        client = DocumentIntelligenceClient(
            AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT,
            AzureKeyCredential(AZURE_DOCUMENT_INTELLIGENCE_KEY),
        )
        poller = client.begin_analyze_document("prebuilt-invoice", document=file_bytes)
        result = poller.result()
    except Exception as exc:
        logger.warning("Azure analysis failed: %s", exc)
        return {"success": False, "error": str(exc)}

    if not result.documents:
        return {"success": False, "error": "No invoice document detected"}

    fields = result.documents[0].fields or {}

    lines: list[Dict[str, Any]] = []
    items = fields.get("Items") or {}
    for item in items.get("valueArray") or []:
        item_fields = item.get("fields") or {}
        description = _safe_content(item_fields.get("Description"))
        amount_field = item_fields.get("Amount") or {}
        amount = amount_field.get("content") or amount_field.get("valueNumber")
        lines.append(
            {
                "description": description,
                "amount": amount,
                "currency": _safe_content(item_fields.get("Currency")),
            }
        )

    total_field = fields.get("InvoiceTotal") or {}
    total = total_field.get("valueNumber")
    if total is None:
        content = str(total_field.get("content") or "")
        try:
            total = float(content.replace(",", "").strip())
        except (TypeError, ValueError):
            total = None

    return {
        "success": True,
        "raw_text": result.content or "",
        "client_name": _safe_content(fields.get("CustomerName")),
        "invoice_no": _safe_content(fields.get("InvoiceId")),
        "invoice_date": _safe_content(fields.get("InvoiceDate")),
        "currency": _safe_content(fields.get("Currency")),
        "total_amount": total,
        "lines": lines,
    }
