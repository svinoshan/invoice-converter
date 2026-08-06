"""Row-level validation for extracted/edited invoice data.

Each row represents exactly one invoice (see ``invoice_to_row``). Validation
produces warnings only: the app lets users save or export even when issues
exist, but surfaces every problem clearly and marks rows that need review.
"""

from __future__ import annotations

import math
from typing import Dict, List, Union

try:
    import pandas as pd
except ImportError:  # pandas is optional for the pure validation logic
    pd = None  # type: ignore[assignment]

# Columns that must be present before a row may be auto-saved. "Sub total"
# is deliberately NOT required here: invoices without an explicit Sub Total
# label are common and can still be stored (only a missing invoice number or
# total amount forces review, see needs_review). The subtotal warning in
# validate_row() is therefore informational, not blocking.
REQUIRED_COLUMNS = (
    "Client",
    "Invoice No",
    "Invoice Date",
    "Description",
    "Total amount",
    "Currency",
)


def _to_float(value: object) -> Union[float, None]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def validate_row(row: Dict) -> List[str]:
    """Return a list of warning messages for one invoice row (empty when OK)."""
    issues: List[str] = []

    client = str(row.get("Client") or "").strip()
    invoice_no = str(row.get("Invoice No") or "").strip()
    invoice_date = str(row.get("Invoice Date") or "").strip()
    description = str(row.get("Description") or "").strip()
    currency = str(row.get("Currency") or "").strip()

    if not client:
        issues.append("Missing client name")
    if not invoice_no:
        issues.append("Missing invoice number")
    if not invoice_date:
        issues.append("Missing invoice date")
    if not description:
        issues.append("Empty description")
    if not currency:
        issues.append("Missing currency")

    subtotal = _to_float(row.get("Sub total"))
    if subtotal is not None and math.isnan(subtotal):
        issues.append("Sub total is not numeric")
    elif subtotal is None:
        issues.append("Sub total is missing (optional)")

    total = _to_float(row.get("Total amount"))
    if total is None or math.isnan(total):
        issues.append("Total amount is missing or not numeric")

    return issues


def needs_review(row: Dict) -> bool:
    """Return True when a row must be reviewed/corrected before saving.

    Records without an invoice number are never upserted automatically;
    missing totals also force a manual review.
    """
    invoice_no = str(row.get("Invoice No") or "").strip()
    total = _to_float(row.get("Total amount"))
    if not invoice_no or total is None:
        return True
    for column in REQUIRED_COLUMNS:
        value = str(row.get(column) or "").strip()
        if not value:
            return True
    return False


def can_auto_save(row: Dict) -> bool:
    """Return True when a parsed row is valid enough to upsert automatically."""
    return not needs_review(row)


def validate_rows(rows: Union[List[Dict], pd.DataFrame]) -> List[Dict]:
    """Validate every row; return ``[{"row": n, "issues": [...]}]`` for bad rows.

    Accepts either a list of dicts or a pandas DataFrame (row numbers are
    1-based and match what the user sees in the data editor).
    """
    records = rows.to_dict("records") if hasattr(rows, "to_dict") else list(rows)

    results: List[Dict] = []
    for index, row in enumerate(records, start=1):
        issues = validate_row(row)
        if issues:
            results.append({"row": index, "issues": issues})
    return results