"""Tesseract OCR fallback for scanned PDFs.

PyMuPDF is used to rasterise each page, then pytesseract runs OCR on the
images. Requires the ``tesseract`` binary to be installed on the host.
"""

from __future__ import annotations

import logging

from utils.logging_config import get_logger

logger = get_logger("ocr_service")


def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:
        logger.warning("Tesseract is not available: %s", exc)
        return False


def extract_with_ocr(file_bytes: bytes, dpi: int = 200) -> str:
    """Render every page to an image and run Tesseract OCR on it.

    Returns the concatenated OCR text, or an empty string on failure.
    """
    try:
        import fitz  # PyMuPDF, used as the rasteriser
        from PIL import Image
    except ImportError:
        logger.warning("PyMuPDF/Pillow are required for OCR rendering")
        return ""

    if not _tesseract_available():
        return ""

    try:
        import pytesseract
    except ImportError:
        logger.warning("pytesseract is not installed")
        return ""

    parts: list[str] = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            pixmap = page.get_pixmap(dpi=dpi)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            parts.append(pytesseract.image_to_string(image))
        doc.close()
    except Exception as exc:
        logger.warning("OCR failed: %s", exc)
        return ""

    return "\n".join(parts)
