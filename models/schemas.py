"""Shared data structures used across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class InvoiceLine:
    """A single invoice line item (used during parsing, not for export)."""

    description: str = ""
    amount: float = 0.0
    currency: str = ""
    line_order: int = 1


@dataclass
class ParsedInvoice:
    """Invoice-level parse result - exactly one of these per PDF."""

    client_name: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    currency: str = ""
    # Line item descriptions are kept as a list internally and only joined
    # with newlines right before display/export.
    descriptions: List[str] = field(default_factory=list)
    total_amount: Optional[float] = None
    # Raw line items, preserved for reference / database granularity.
    lines: List[InvoiceLine] = field(default_factory=list)
    raw_text: str = ""
    extraction_method: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def description_text(self) -> str:
        """Combined descriptions, one per line (matches the Excel cell)."""
        return "\n".join(self.descriptions)