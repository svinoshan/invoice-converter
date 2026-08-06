"""Batch PDF processing with automatic chunking and auto-upsert.

The UI never asks the user for a batch size: :func:`calculate_batch_size`
picks a safe chunk size from the number of files and their average size, and
:func:`process_files` loops through the upload internally in those chunks.

After parsing, records that are valid (invoice_no + total amount present) are
upserted to Supabase immediately. Invalid records are returned in the preview
so the user can correct them on the Upload & Process page.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from services import invoice_parser, pdf_extractor, supabase_service, validators

ProgressCallback = Callable[[int, int, str], None]


def calculate_batch_size(uploaded_files: List[Dict]) -> int:
    """Return a safe internal chunk size for ``uploaded_files``.

    - No files -> 0
    - 20 files or fewer -> process everything in one run
    - Average file size <= 1 MB -> 50
    - Average file size <= 5 MB -> 25
    - Otherwise (large PDFs) -> 10
    """
    files = list(uploaded_files or [])
    if not files:
        return 0
    total = len(files)
    if total <= 20:
        return total
    average_size = sum(len(f.get("data") or b"") for f in files) / total
    if average_size <= 1_000_000:
        return 50
    if average_size <= 5_000_000:
        return 25
    return 10


def _row_from_parsed(parsed, file_name: str, raw_text: str) -> Dict:
    row = invoice_parser.invoice_to_row(parsed)
    row["Source file"] = file_name
    row["Raw text"] = raw_text
    return row


def _record_from_row(row: Dict, file_name: str, raw_text: str) -> Dict:
    from utils.ui_helpers import row_to_record

    record = row_to_record(row, source_file_name=file_name, raw_text=raw_text)
    record["raw_text"] = raw_text
    return record


def _file_status(name: str) -> Dict:
    return {"status": "Processing", "message": "Extracting text...", "method": "", "invoice_no": ""}


def process_files(
    files: List[Dict],
    options: Dict[str, bool],
    batch_size: int = 0,
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict:
    """Process ``files`` in internal chunks; upsert valid records.

    Returns ``{"statuses": ..., "preview": [...], "log": [...], "summary": {...}}``
    where statuses is keyed by file name, preview rows are editor-ready, log
    holds upsert activity and summary holds compact batch counts.
    """
    files = list(files or [])
    if batch_size <= 0:
        batch_size = calculate_batch_size(files)
    if not files or batch_size <= 0:
        return {"statuses": {}, "preview": [], "log": [], "summary": {
            "processed": 0, "success": 0, "needs_review": 0, "failed": 0,
            "inserted_updated": 0, "batch_size": 0, "files": [],
        }}

    statuses: Dict[str, Dict] = {}
    preview_by_invoice: Dict[str, Dict] = {}
    log: List[Dict] = []
    inserted = 0
    done = 0
    total = len(files)

    for start in range(0, total, batch_size):
        chunk = files[start : start + batch_size]
        for file_item in chunk:
            name = file_item["name"]
            statuses[name] = _file_status(name)
            try:
                text, method, azure_fields = pdf_extractor.extract_text_from_pdf(
                    file_item["data"],
                    azure_enabled=options.get("azure_enabled", False),
                    ocr_enabled=options.get("ocr_enabled", True),
                )
                parsed = invoice_parser.parse_invoice(
                    text, file_name=name, extraction_method=method, hint=azure_fields
                )
                row = _row_from_parsed(parsed, name, text)
                if method == "none":
                    # Nothing usable was extracted - report the failure in
                    # statuses but never show a junk row in the review table.
                    statuses[name] = {
                        "status": "Failed",
                        "message": "Could not extract usable text from this PDF.",
                        "method": "none",
                        "invoice_no": "",
                    }
                    done += 1
                    if progress_cb is not None:
                        progress_cb(done, total, f"Processed {name}")
                    continue

                # One row per invoice - never duplicate in the preview.
                # Only dedupe on real invoice numbers: rows without one
                # (needs review) must each stay visible so the user can
                # correct every file, and "" must never collide as a key.
                if row["Invoice No"]:
                    preview_by_invoice[row["Invoice No"]] = row
                else:
                    preview_by_invoice[f"__missing__:{name}"] = row

                can_auto = validators.can_auto_save(row)
                if can_auto:
                    if supabase_service.is_enabled():
                        supabase_service.upsert_invoice_record(
                            _record_from_row(row, name, text)
                        )
                        inserted += 1
                        log.append(
                            {
                                "File": name,
                                "Invoice No": row["Invoice No"],
                                "Result": "Saved",
                            }
                        )
                        status = "Success"
                        message = f"Processed and upserted with {method}."
                    else:
                        status = "Success"
                        message = (
                            f"Extracted with {method} (Supabase disabled - "
                            "preview only)."
                        )
                else:
                    status = "Needs review"
                    message = (
                        f"Extracted with {method} but needs review "
                        f"({len(parsed.warnings)} warning(s))."
                    )
                statuses[name] = {
                    "status": status,
                    "message": message,
                    "method": method,
                    "invoice_no": row["Invoice No"] or "",
                }
            except Exception as exc:  # noqa: BLE001 - friendly per-file message
                statuses[name] = {
                    "status": "Failed",
                    "message": f"Error: {exc}",
                    "method": "",
                    "invoice_no": "",
                }
            done += 1
            if progress_cb is not None:
                progress_cb(done, total, f"Processed {name}")

    summary = {
        "processed": done,
        "success": sum(1 for s in statuses.values() if s["status"] == "Success"),
        "needs_review": sum(
            1 for s in statuses.values() if s["status"] == "Needs review"
        ),
        "failed": sum(1 for s in statuses.values() if s["status"] == "Failed"),
        "inserted_updated": inserted,
        "batch_size": batch_size,
        "files": [f["name"] for f in files],
    }
    return {
        "statuses": statuses,
        "preview": list(preview_by_invoice.values()),
        "log": log,
        "summary": summary,
    }