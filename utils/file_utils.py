"""Small file/name helpers used across the app."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


def sanitize_filename(name: str) -> str:
    """Strip path components and keep only filesystem-safe characters."""
    base = str(name).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base or "upload.pdf"


def unique_filename(name: str) -> str:
    """Return ``name`` with a short random suffix to avoid storage collisions."""
    safe = sanitize_filename(name)
    stem, _, ext = safe.rpartition(".")
    if not ext:
        return f"{safe}_{uuid.uuid4().hex[:8]}"
    return f"{stem}_{uuid.uuid4().hex[:8]}.{ext}"


def get_export_filename(prefix: str = "invoice_export") -> str:
    """Return an export file name like ``invoice_export_20260608_103000.xlsx``."""
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"


def human_readable_bytes(num_bytes: int) -> str:
    """Format a byte count in a human readable way (e.g. 1.5 MB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def file_extension(filename: str) -> str:
    """Return the lower-case extension of ``filename`` (e.g. 'pdf')."""
    return Path(filename).suffix.lower().lstrip(".")


def format_percent(value: float, digits: int = 1) -> str:
    """Format a fraction (0..1) as a percentage string."""
    return f"{value * 100:.{digits}f}%"


def shorten_text(text: Optional[str], limit: int = 200) -> str:
    """Truncate text for log messages."""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."
