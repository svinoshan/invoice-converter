"""Excel generation with openpyxl.

One row is written per invoice. The ``Description`` cell may contain
newline-separated descriptions, so wrap text is enabled and row heights grow
with the number of lines.

Export layout (exact headers):
``Client, Invoice No, Invoice Date, Description, Total amount, Currency``
"""

from __future__ import annotations

import io
import math
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

EXPORT_COLUMNS = [
    "Client",
    "Invoice No",
    "Invoice Date",
    "Description",
    "Total amount",
    "Currency",
]

_DESCRIPTION_COLUMN = "Description"
_AMOUNT_COLUMN = "Total amount"
_BASE_ROW_HEIGHT = 20  # points, default
_LINE_HEIGHT = 15.0  # approx points per text line in a wrapped cell
_MAX_ROW_HEIGHT = 300.0
_MAX_COLUMN_WIDTH = 60


def _to_excel_value(column: str, value: object) -> object:
    """Coerce a record value into something openpyxl can write safely."""
    if column == _AMOUNT_COLUMN:
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

    # Header row: bold, centered, frozen.
    for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.freeze_panes = "A2"

    # Data rows: one row per invoice.
    for row_idx, record in enumerate(rows, start=2):
        for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
            cell = worksheet.cell(
                row=row_idx,
                column=col_idx,
                value=_to_excel_value(title, record.get(title)),
            )
            if title == _AMOUNT_COLUMN:
                cell.number_format = "#,##0.00"
            if title == _DESCRIPTION_COLUMN:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

        # Grow the row height to fit newline-separated descriptions.
        description = str(record.get(_DESCRIPTION_COLUMN) or "")
        line_count = description.count("\n") + 1 if description else 1
        worksheet.row_dimensions[row_idx].height = min(
            max(_BASE_ROW_HEIGHT, line_count * _LINE_HEIGHT), _MAX_ROW_HEIGHT
        )

    # Auto-fit column widths. For the description column use the longest
    # single line (wrapped cells), for other columns the longest value.
    for col_idx, title in enumerate(EXPORT_COLUMNS, start=1):
        max_length = len(title)
        for cell in worksheet.iter_rows(
            min_col=col_idx, max_col=col_idx, min_row=2, values_only=False
        ):
            value = cell[0].value
            if value is None:
                continue
            if title == _DESCRIPTION_COLUMN:
                longest_line = max((len(line) for line in str(value).split("\n")), default=0)
                max_length = max(max_length, longest_line)
            else:
                max_length = max(max_length, len(str(value)))
        worksheet.column_dimensions[get_column_letter(col_idx)].width = min(
            max_length + 4, _MAX_COLUMN_WIDTH
        )

    return workbook


def generate_excel_bytes(rows: List[Dict]) -> bytes:
    """Build an Excel workbook from invoice rows and return it as bytes."""
    workbook = _build_workbook(rows)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def generate_template_bytes() -> bytes:
    """Return a template workbook with headers and one empty row."""
    return generate_excel_bytes([{column: "" for column in EXPORT_COLUMNS}])