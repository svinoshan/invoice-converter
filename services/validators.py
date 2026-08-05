"""Row-level validation for extracted/edited invoice data.

Validation produces warnings only: the app lets users export or save even
when issues exist, but surfaces every problem clearly.
"""

from __future__ import annotations

import math
from typing import Dict, List, Union

try:
    import pandas as pd
except ImportError:  # pandas is optional for the pure validation logic
    pd = None  # type: ignore[assignment]


def validate_row(row: Dict) -> List[str]:
    """Return a list of warning messages for one row (empty when OK)."""
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

    amount_value = row.get("Total amount")
    numeric: Union[float, None] = None
    if isinstance(amount_value, (int, float)) and not isinstance(amount_value, bool):
        numeric = float(amount_value)
    else:
        try:
            numeric = float(str(amount_value).replace(",", ""))
        except (TypeError, ValueError):
            numeric = None
    if numeric is None or math.isnan(numeric):
        issues.append("Amount is not numeric")

    return issues


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
