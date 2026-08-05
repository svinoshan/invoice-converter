"""Shared data structures used across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class InvoiceLine:
    """A single invoice line item."""

    description: str = ""
    amount: float = 0.0
    currency: str = ""
    line_order: int = 1


@dataclass
class ParsedInvoice:
    """Result of parsing one invoice PDF."""

    client_name: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    currency: str = ""
    lines: List[InvoiceLine] = field(default_factory=list)
    raw_text: str = ""
    extraction_method: str = ""
    warnings: List[str] = field(default_factory=list)
