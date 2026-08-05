"""Rule-based extraction of invoice metadata and line items from raw text.

The parser is intentionally heuristic: invoices vary wildly between suppliers,
so every extraction is paired with validation warnings in the UI and can be
manually corrected before saving or exporting.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Dict, List, Optional

from dateutil import parser as date_parser

from models.schemas import InvoiceLine, ParsedInvoice

# ---------------------------------------------------------------------------
# Currency normalisation
# ---------------------------------------------------------------------------

_CURRENCY_MAP: Dict[str, str] = {
    "€": "Euro",
    "eur": "Euro",
    "euro": "Euro",
    "euros": "Euro",
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "rs": "LKR",
    "rs.": "LKR",
    "lkr": "LKR",
    "rupees": "LKR",
    "slrs": "LKR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "₹": "INR",
    "inr": "INR",
    "aed": "AED",
    "aud": "AUD",
    "cad": "CAD",
    "sgd": "SGD",
    "chf": "CHF",
    "thb": "THB",
    "myr": "MYR",
    "rm": "MYR",
    "php": "PHP",
}

_CURRENCY_SCAN_RE = re.compile(
    r"(\bEUR\b|\bEuro(?:s)?\b|€|\bUSD\b|\$|\bLKR\b|\bRs\.?\b|\bRupees\b|"
    r"\bGBP\b|£|\bJPY\b|¥|\bINR\b|₹|\bAED\b|\bAUD\b|\bCAD\b|\bSGD\b|"
    r"\bCHF\b|\bTHB\b|\bMYR\b|\bRM\b|\bPHP\b)",
    re.IGNORECASE,
)


def normalize_currency(value: object) -> str:
    """Map a raw currency token (symbol, code or word) to a canonical name."""
    if value is None:
        return ""
    token = str(value).strip().strip(":;")
    token = re.sub(r"\s+", " ", token).lower()
    if token in _CURRENCY_MAP:
        return _CURRENCY_MAP[token]
    if token.endswith("s") and token[:-1] in _CURRENCY_MAP:
        return _CURRENCY_MAP[token[:-1]]
    return ""


def extract_currency(text: str) -> str:
    """Return the most frequently occurring currency in ``text``."""
    if not text:
        return ""
    counts: Counter[str] = Counter()
    for token in _CURRENCY_SCAN_RE.findall(text):
        canonical = normalize_currency(token)
        if canonical:
            counts[canonical] += 1
    return counts.most_common(1)[0][0] if counts else ""


# ---------------------------------------------------------------------------
# Amount cleaning
# ---------------------------------------------------------------------------


def clean_amount(value: object) -> Optional[float]:
    """Convert a raw amount value (string or number) to ``float`` or ``None``.

    Handles thousands separators, currency symbols/codes and parentheses for
    negative values. Returns ``None`` when the value cannot be interpreted.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.endswith("-") and re.search(r"\d", s):
        negative = True
        s = s[:-1]

    s = s.replace(",", "")
    s = re.sub(r"[€$£¥₹]", "", s)
    s = re.sub(
        r"^\s*(?:rs\.?|lkr|eur|usd|gbp|inr|aed|aud|cad|sgd|chf|thb|myr|php|rm)\s*\.?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()

    try:
        number = float(s)
    except ValueError:
        return None
    return -number if negative else number


# ---------------------------------------------------------------------------
# Date handling
# ---------------------------------------------------------------------------

_MONTH_MAP: Dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_ISO_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$")
_MONTH_NAME_DATE_RE = re.compile(
    r"^(\d{1,2})[-/.\s]?([A-Za-z]{3,9})[-/.\s]?(\d{2,4})$", re.IGNORECASE
)
_DATE_LABEL_RE = re.compile(
    r"(?:invoice\s*)?(?:date|dated)\s*[:#]?\s*([A-Za-z0-9][^\n]{2,40}?)(?:\n|$)",
    re.IGNORECASE,
)
_DATE_FALLBACK_RE = re.compile(
    r"\b(\d{1,2}[-/.\s][A-Za-z]{3,9}[-/.]\d{2,4})\b", re.IGNORECASE
)


def _two_digit_year(year: int) -> int:
    if year < 100:
        return 2000 + year if year <= 49 else 1900 + year
    return year


def _safe_date(day: int, month: int, year: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_date(value: object) -> str:
    """Parse a date string into the canonical ``d-mmm-yy`` format (8-Jun-26).

    Supports ISO (2026-06-08), numeric (08/06/2026, 8/6/26) and month-name
    (8-Jun-26) styles. Returns an empty string when the value cannot be parsed.
    """
    if value is None:
        return ""
    raw = str(value).strip().strip(".,;:()")
    if not raw:
        return ""

    parsed: Optional[date] = None

    m = _ISO_DATE_RE.match(raw)
    if m:
        parsed = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    else:
        m = _NUMERIC_DATE_RE.match(raw)
        if m:
            parsed = _safe_date(
                int(m.group(1)), int(m.group(2)), _two_digit_year(int(m.group(3)))
            )
        else:
            m = _MONTH_NAME_DATE_RE.match(raw)
            if m:
                month_name = m.group(2).lower()
                month = _MONTH_MAP.get(month_name) or _MONTH_MAP.get(month_name[:3])
                if month:
                    parsed = _safe_date(
                        int(m.group(1)), month, _two_digit_year(int(m.group(3)))
                    )

    if parsed is None:
        try:
            parsed = date_parser.parse(raw, dayfirst=True).date()
        except (TypeError, ValueError, OverflowError):
            return ""

    return parsed.strftime("%d-%b-%y").lstrip("0")


def extract_invoice_date(text: str) -> str:
    """Find and normalise the invoice date in ``text``."""
    if not text:
        return ""

    for match in _DATE_LABEL_RE.finditer(text):
        candidate = match.group(1).strip().strip(".:,")
        tokens = candidate.split()
        for size in (1, 2):
            norm = normalize_date(" ".join(tokens[:size]))
            if norm:
                return norm

    for line in text.splitlines()[:20]:
        match = _DATE_FALLBACK_RE.search(line)
        if match:
            norm = normalize_date(match.group(1))
            if norm:
                return norm

    return ""


# ---------------------------------------------------------------------------
# Invoice number
# ---------------------------------------------------------------------------

_INVOICE_NO_RE = re.compile(
    r"(?:invoice\s*(?:ref(?:erence)?|no\.?|number|#)|inv\s*no\.?)\s*[:#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9/\\\-._ ]{2,80})",
    re.IGNORECASE,
)
_BARE_REF_RE = re.compile(r"\b([A-Z0-9]{2,10}(?:/[-A-Z0-9]+){2,})\b", re.IGNORECASE)


def _clean_invoice_ref(raw: str) -> str:
    raw = raw.strip().strip(".:,;")
    raw = re.split(r"\b(date|dated)\s*[:#]?\b", raw, maxsplit=1, flags=re.I)[0].strip()
    tokens = raw.split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0].rstrip(".,:;")
    first = tokens[0]
    if "/" in first or ("-" in first and any(ch.isdigit() for ch in first)):
        return first.rstrip(".,:;")
    return " ".join(tokens[:2]).rstrip(".,:;")


def extract_invoice_number(text: str) -> str:
    """Extract the invoice reference/number from ``text``."""
    if not text:
        return ""
    match = _INVOICE_NO_RE.search(text)
    if match:
        cleaned = _clean_invoice_ref(match.group(1))
        if cleaned:
            return cleaned
    match = _BARE_REF_RE.search(text)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Client name
# ---------------------------------------------------------------------------

_CLIENT_LABEL_RE = re.compile(
    r"(?:client|customer|buyer|bill\s*to|consignee|sold\s*to|"
    r"messrs\.?\s*|m\.?\s*[/.]\s*s\.?\s*|m\.\s*)"
    r"\s*[:#.]?\s*([A-Za-z0-9][^\n]{2,80})",
    re.IGNORECASE,
)
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(ltd\.?|limited|pvt\.?|private|co(?:rp)?\.?|corp\.?|corporation|inc\.?|"
    r"llc|gmbh|b\.v\.?|plc|srl|llp|company|enterprises?|exports?|imports?|"
    r"industries|group|holdings?|associates?|trading)\b",
    re.IGNORECASE,
)
_IGNORED_PREFIXES = (
    "invoice", "ref", "date", "dated", "no.", "no:", "no ", "tel", "telephone",
    "phone", "fax", "email", "e-mail", "web", "www", "http", "page", "currency",
    "terms", "amount", "total", "qty", "quantity", "rate", "attn", "attention",
    "for", "vat", "tax", "bank", "swift", "a/c", "acct", "account",
)


def _clean_label_value(raw: str) -> str:
    """Trim trailing label noise (e.g. 'Acme Ltd, Invoice No: INV-1')."""
    s = re.sub(r"\s+", " ", raw.strip().strip(":#.,;-")).strip()
    s = re.split(
        r"\b(invoice\s*(?:no|ref|number|date)|inv\s*no|tel(?:ephone)?|fax|"
        r"email|e-mail|www|http|vat\s*no|date|dated|currency)\b",
        s,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return s.strip(" ,:;-")


def _looks_like_company(line: str) -> bool:
    line = line.strip()
    if not (3 <= len(line) <= 70):
        return False
    lower = line.lower()
    if lower.startswith(_IGNORED_PREFIXES):
        return False
    if not _COMPANY_SUFFIX_RE.search(line):
        return False
    return True


def extract_client_name(text: str) -> str:
    """Extract the client/company name from ``text``."""
    if not text:
        return ""

    for match in _CLIENT_LABEL_RE.finditer(text):
        name = _clean_label_value(match.group(1))
        if _looks_like_company(name):
            return name

    lines = [ln.strip() for ln in text.splitlines()[:15] if ln.strip()]

    for line in lines:
        if _looks_like_company(line):
            return _clean_label_value(line)

    # The name often sits directly above the invoice reference block.
    ref_match = _INVOICE_NO_RE.search(text)
    if ref_match:
        preceding = text[: ref_match.start()]
        prior_lines = [ln.strip() for ln in preceding.splitlines() if ln.strip()][-3:]
        for line in reversed(prior_lines):
            if _looks_like_company(line):
                return _clean_label_value(line)

    return _clean_label_value(lines[0]) if lines else ""


# ---------------------------------------------------------------------------
# Line items
# ---------------------------------------------------------------------------

_EXCLUDED_DESC_FRAGMENTS = (
    "sub total", "subtotal", "tax amount", "vat", "total", "exchange rate",
    "payment terms", "payment instructions", "bank", "swift", "a/c no",
    "account no", "account number", "authorised signatory", "authorized signatory",
    "amount in words", "balance due", "due date", "terms and conditions",
    "e.&o.e", "e & oe", "e&oe", "grand total", "total amount", "total due",
    "net amount", "amount payable", "payable", "less: advance", "advance paid",
    "credited to our", "kindly credit", "cheque", "cheque no", "all remittances",
    "please transfer", "for the payment", "we request", "in settlement",
    "thank you", "yours faithfully", "yours sincerely", "best regards",
)

_HEADER_ONLY_DESC = {
    "description", "particulars", "particulars of", "item", "items",
    "item description", "details", "detail", "amount", "amounts", "rate",
    "rates", "qty", "quantity", "unit", "unit price", "unit rate", "value",
    "values", "remarks", "no", "no.", "sl no", "sl.no", "serial no", "product",
    "product name", "code", "uom", "ref", "ref:",
}

_SKIP_DESC_PREFIXES = (
    "invoice", "ref:", "page", "tel:", "phone:", "fax:", "email:", "e-mail:",
    "www", "http", "bank:", "swift", "vat", "a/c", "date :", "date:",
    "dated", "total", "sub total", "currency", "bill to", "ship to", "client",
    "customer", "attn", "balance", "no.", "no:", "no ",
)


def _is_year_like(token: str) -> bool:
    digits = token.replace(",", "")
    if not re.fullmatch(r"\d{4}", digits):
        return False
    return 1900 <= int(digits) <= 2100


def _is_amount_token(token: str) -> bool:
    if not token or not re.search(r"\d", token):
        return False
    cleaned = token.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", cleaned):
        return False
    if "." not in cleaned and _is_year_like(cleaned):
        return False
    return True


def _parse_item_line(line: str, default_currency: str) -> Optional[InvoiceLine]:
    """Try to interpret one text line as ``description <amount> [currency]``."""
    stripped = line.strip()
    if not stripped or len(stripped) < 4:
        return None

    tokens = stripped.split()
    for i in range(len(tokens) - 1, -1, -1):
        raw_amount = tokens[i].strip("(),")
        if not _is_amount_token(raw_amount):
            continue
        amount = clean_amount(raw_amount)
        if amount is None:
            continue

        desc_tokens = tokens[:i]
        currency = ""

        if i - 1 >= 0:
            currency = normalize_currency(tokens[i - 1])
            if currency:
                desc_tokens = tokens[: i - 1]
        elif i + 1 < len(tokens):
            currency = normalize_currency(tokens[i + 1])

        # Table layout "<desc> <qty> <rate> <amount>" - keep the final amount.
        if (
            len(desc_tokens) >= 3
            and _is_amount_token(desc_tokens[-1])
            and desc_tokens[-2].isdigit()
        ):
            desc_tokens = desc_tokens[:-2]
        # "@ rate amount" layout, e.g. "Storage fee @ 10.00 20.00".
        elif (
            len(desc_tokens) >= 2
            and desc_tokens[-2].lower() == "@"
            and _is_amount_token(desc_tokens[-1])
        ):
            desc_tokens = desc_tokens[:-2]
        # Quantity pattern, e.g. "Gadgets 2 x 50.00".
        elif (
            len(desc_tokens) >= 2
            and desc_tokens[-1].lower() in ("x", "@", "*")
            and desc_tokens[-2].isdigit()
        ):
            quantity = int(desc_tokens[-2])
            amount = round(amount * quantity, 2)
            desc_tokens = desc_tokens[:-2]

        description = " ".join(desc_tokens).strip(" -•*()[]:.,;\"'")
        if not description or len(description) < 3:
            return None
        if not re.search(r"[A-Za-z]", description):
            return None

        lower = description.lower()
        if any(fragment in lower for fragment in _EXCLUDED_DESC_FRAGMENTS):
            return None
        if lower in _HEADER_ONLY_DESC or lower.startswith(_SKIP_DESC_PREFIXES):
            return None

        return InvoiceLine(
            description=description,
            amount=amount,
            currency=currency or default_currency,
        )
    return None


def extract_line_items(text: str, default_currency: str = "") -> List[InvoiceLine]:
    """Parse all plausible line items out of ``text`` in order."""
    items: List[InvoiceLine] = []
    order = 0
    for raw_line in text.splitlines():
        item = _parse_item_line(raw_line.strip(), default_currency)
        if item is None:
            continue
        order += 1
        item.line_order = order
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def parse_invoice(
    text: str,
    file_name: str = "",
    extraction_method: str = "",
    hint: Optional[Dict] = None,
) -> ParsedInvoice:
    """Parse invoice text into a :class:`ParsedInvoice`.

    ``hint`` is an optional dict (e.g. produced by Azure Document Intelligence)
    whose non-empty values take precedence over the rule-based extraction.
    """
    hint = hint or {}

    invoice_no = (
        str(hint.get("invoice_no") or "").strip() or extract_invoice_number(text)
    )
    client_name = (
        str(hint.get("client_name") or "").strip() or extract_client_name(text)
    )
    invoice_date = normalize_date(hint.get("invoice_date")) or extract_invoice_date(
        text
    )
    currency = str(hint.get("currency") or "").strip() or extract_currency(text)
    currency = normalize_currency(currency) or currency

    lines: List[InvoiceLine] = []
    raw_hint_lines = hint.get("lines") or []
    if raw_hint_lines:
        for order, item in enumerate(raw_hint_lines, start=1):
            description = str(item.get("description") or "").strip()
            amount = clean_amount(item.get("amount")) or 0.0
            line_currency = normalize_currency(item.get("currency")) or currency
            if not description and not amount:
                continue
            lines.append(
                InvoiceLine(
                    description=description,
                    amount=amount,
                    currency=line_currency,
                    line_order=order,
                )
            )
    else:
        lines = extract_line_items(text, default_currency=currency)

    warnings: List[str] = []
    if not client_name:
        warnings.append("Missing client name")
    if not invoice_no:
        warnings.append("Missing invoice number")
    if not invoice_date:
        warnings.append("Missing invoice date")
    if not currency:
        warnings.append("Missing currency")
    if not lines:
        warnings.append("No line items detected")

    return ParsedInvoice(
        client_name=client_name,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        currency=currency,
        lines=lines,
        raw_text=text,
        extraction_method=extraction_method,
        warnings=warnings,
    )


EXPORT_COLUMN_KEYS = (
    "Client",
    "Invo No",
    "Invo Date",
    "description",
    "amount",
    "currancy",
)


def _row_from_invoice(invoice: ParsedInvoice, line: InvoiceLine) -> Dict[str, object]:
    """Flatten one invoice line into an exportable row."""
    return {
        "Client": invoice.client_name,
        "Invo No": invoice.invoice_no,
        "Invo Date": invoice.invoice_date,
        "description": line.description,
        "amount": line.amount,
        "currancy": line.currency or invoice.currency,
    }


def flatten_invoice(invoice: ParsedInvoice, file_name: str = "") -> List[Dict[str, object]]:
    """Expand a parsed invoice into one row per line item.

    When no line items were found a single placeholder row is returned so the
    user can still manually fill in the details in the editor.
    """
    if invoice.lines:
        return [_row_from_invoice(invoice, line) for line in invoice.lines]
    return [_row_from_invoice(invoice, InvoiceLine())]
