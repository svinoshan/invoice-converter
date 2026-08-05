"""Central configuration access.

Secrets are resolved in this order:
1. Streamlit secrets (``.streamlit/secrets.toml`` or dashboard) when running
   under Streamlit.
2. Environment variables loaded from a local ``.env`` file.

The service role key is only ever read server-side and is never sent to the
browser.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the value for ``key`` from Streamlit secrets or environment."""
    try:
        from streamlit import secrets

        if key in secrets:
            return secrets[key]
    except Exception:
        # Not running under Streamlit, or no secrets file present.
        pass
    return os.getenv(key, default)


SUPABASE_URL: Optional[str] = _get_secret("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: Optional[str] = _get_secret("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET: str = _get_secret("SUPABASE_BUCKET", "invoice-pdfs") or "invoice-pdfs"

# Master switch for the "Save to Supabase" feature. Keep it false (default)
# to run the app purely as a local PDF -> Excel converter.
_ENABLE_SUPABASE_SAVE = _get_secret("ENABLE_SUPABASE_SAVE", "false")
ENABLE_SUPABASE_SAVE: bool = str(_ENABLE_SUPABASE_SAVE).strip().lower() in (
    "1", "true", "yes", "on",
)

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: Optional[str] = _get_secret(
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
)
AZURE_DOCUMENT_INTELLIGENCE_KEY: Optional[str] = _get_secret(
    "AZURE_DOCUMENT_INTELLIGENCE_KEY"
)
