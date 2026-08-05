"""Excel generation with openpyxl.

Column names are fixed to match the expected finance template exactly:
``Client, Invo No, Invo Date, description, amount, currancy``.
"""

from __future__ import annotations

import io
import math
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

EXPORT_COLUMNS = ["Client", "Invo No", "Invo Date", "description", "amount", "currancy"]


def _to_excel_value(column: str, value: object) -> object:
    """Coerce a record value into something openpyxl can write safely."""
    if column == "amount":
        if value is None or value == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(number) else number
    if value is None:
        return ""
    return str(value)


def _build_workbook(rows: List[Dict]) -> Workbook:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Invoices"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")

    for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_idx, record in enumerate(rows, start=2):
        for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
            cell = worksheet.cell(
                row=row_idx,
                column=col_idx,
                value=_to_excel_value(title, record.get(title)),
            )
            if title == "amount":
                cell.number_format = "#,##0.00"

    # Auto-fit column widths (capped to keep the file readable).
    for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
        max_length = len(title)
        for cell in worksheet.iter_rows(
            min_col=col_idx, max_col=col_idx, min_row=2, values_only=True
        ):
            if cell is not None:
                max_length = max(max_length, len(str(cell)))
        worksheet.column_dimensions[get_column_letter(col_idx)].width = min(
            max_length + 4, 60
        )

    worksheet.freeze_panes = "A2"
    return workbook


def generate_excel_bytes(rows: List[Dict]) -> bytes:
    """Build an Excel workbook from export rows and return it as bytes."""
    workbook = _build_workbook(rows)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def generate_template_bytes() -> bytes:
    """Return a template workbook with headers and one empty row."""
    return generate_excel_bytes([{column: "" for column in EXPORT_COLUMNS}])
