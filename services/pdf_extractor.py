"""PDF text extraction with a fallback chain.

Priority:
1. pdfplumber
2. PyMuPDF (fitz) when pdfplumber output is empty/too short
3. Tesseract OCR (renders pages to images with PyMuPDF)
4. Azure Document Intelligence (optional, when enabled and configured)
"""

from __future__ import annotations

import io
import logging
from typing import Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("pdf_extractor")

MIN_TEXT_CHARS = 40


def _has_content(text: str) -> bool:
    """Decide whether extracted text is usable (not empty and has letters)."""
    stripped = (text or "").strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return False
    return any(ch.isalpha() for ch in stripped)


def _extract_pdfplumber(file_bytes: bytes) -> str:
    """Extract text with pdfplumber (best layout fidelity)."""
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber is not installed")
        return ""
    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    parts.append(page_text)
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("pdfplumber extraction failed: %s", exc)
        return ""


def _extract_pymupdf(file_bytes: bytes) -> str:
    """Extract text with PyMuPDF (handles many scanned layouts poorly but
    is a solid second pass when pdfplumber yields nothing)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF is not installed")
        return ""
    try:
        parts: list[str] = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            page_text = page.get_text("text") or ""
            if page_text.strip():
                parts.append(page_text)
        doc.close()
        return "\n".join(parts)
    except Exception as exc:
        logger.warning("PyMuPDF extraction failed: %s", exc)
        return ""


def extract_text_from_pdf(
    file_bytes: bytes,
    azure_enabled: bool = False,
    ocr_enabled: bool = True,
) -> Tuple[str, str, Optional[Dict]]:
    """Extract text from PDF bytes using the fallback chain.

    Returns a tuple ``(text, method, azure_fields)`` where ``method`` is one
    of ``pdfplumber | pymupdf | ocr | azure_document_intelligence | none`` and
    ``azure_fields`` is the structured Azure result when that engine was used.
    """
    method = "pdfplumber"
    text = _extract_pdfplumber(file_bytes)

    if not _has_content(text):
        method = "pymupdf"
        text = _extract_pymupdf(file_bytes)

    if not _has_content(text) and ocr_enabled:
        from services.ocr_service import extract_with_ocr

        method = "ocr"
        text = extract_with_ocr(file_bytes)

    azure_fields: Optional[Dict] = None
    if not _has_content(text) and azure_enabled:
        from services.azure_invoice_service import analyze_invoice

        result = analyze_invoice(file_bytes)
        if result.get("success"):
            azure_fields = result
            if result.get("raw_text"):
                text = result["raw_text"]
            method = "azure_document_intelligence"

    if not _has_content(text):
        method = "none"

    return text, method, azure_fields
