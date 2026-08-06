"""Shared data structures used across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InvoiceLine:
    """A single invoice line item (used during description extraction)."""

    description: str = ""
    amount: float = 0.0
    currency: str = ""
    line_order: int = 1


@dataclass
class ParsedInvoice:
    """Invoice-level parse result - exactly one of these per PDF.

    Field names match the clean internal schema used by Supabase records
    (``client``, ``invoice_no``, ``sub_total``, ``total_amount``, ...).
    Descriptions stay a list internally and are only joined with newlines
    right before display, saving or export.
    """

    client: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    descriptions: List[str] = field(default_factory=list)
    sub_total: Optional[float] = None
    total_amount: Optional[float] = None
    currency: str = ""
    source_file_name: str = ""
    raw_text: str = ""
    extraction_method: str = ""
    extraction_status: str = "processed"
    reviewed: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        """Combined descriptions, one per line (matches the Excel cell)."""
        return "\n".join(self.descriptions)

    def to_supabase_record(self) -> Dict[str, Any]:
        """Return the record payload for the Supabase ``invoice_records`` table."""
        return {
            "client": self.client,
            "invoice_no": self.invoice_no,
            "invoice_date": self.invoice_date,
            "description": self.description,
            "sub_total": self.sub_total,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "source_file_name": self.source_file_name,
            "raw_text": self.raw_text,
            "extraction_status": self.extraction_status,
            "reviewed": self.reviewed,
        }