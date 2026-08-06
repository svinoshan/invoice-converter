"""Rule-based extraction of invoice metadata, descriptions and total amount.

The parser returns *invoice-level* data: one :class:`ParsedInvoice` per PDF,
with all line descriptions kept as a list and the final invoice total amount
extracted separately (never individual line amounts).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Dict, List, Optional, Sequence

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
    r"industries|group|holdings?|associates?|trading|mills?|textiles?|"
    r"garments?|foods?|knitting|sugar|pharma|chemicals?|engineering)\b",
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


def _is_bank_or_payment_line(lower: str) -> bool:
    """True for payment-instruction / bank-detail lines - never the client."""
    return any(
        token in lower
        for token in (" bank", "bank of", "a/c no", "account no",
                      "account type", "swift", "payment", "payable")
    )


def _is_address_line(lower: str) -> bool:
    """True for supplier address/reference lines - never the client."""
    return any(
        token in lower
        for token in ("project_ref", "project ref", "postal code", "suite ",
                      "floor,", "road,", "industrial park", "export processing zone",
                      "building,", "lane,", "colombo", "street")
    )


def _looks_like_company(line: str) -> bool:
    line = line.strip()
    if not (3 <= len(line) <= 70):
        return False
    lower = line.lower()
    if lower.startswith(_IGNORED_PREFIXES):
        return False
    if not _COMPANY_SUFFIX_RE.search(line):
        return False
    if _is_bank_or_payment_line(lower) or _is_address_line(lower):
        return False
    return True


_ITEM_HEADER_WORDS = (
    "description", "particulars", "quantity", "qty", "rate", "amount",
    "total", "unit", "price", "value", "remarks", "sl no", "sl.no",
    "serial no", "item", "details", "discount", "net",
)


def _looks_like_name(text: str) -> bool:
    """Weak company check: 2+ alphabetic words, no pure-digit tokens, and
    not a label/title line. Used as a last resort when the supplier name
    has no legal suffix (e.g. "YARANA TEXTILE MILLS")."""
    s = text.strip()
    if not (3 <= len(s) <= 50):
        return False
    words = s.split()
    alpha_words = [w for w in words if re.search(r"[A-Za-z]{2,}", w)]
    if len(alpha_words) < 2:
        return False
    if any(w.replace(",", "").replace(".", "").isdigit() for w in words):
        return False
    lower = s.lower()
    if lower.startswith(_IGNORED_PREFIXES):
        return False
    if _is_bank_or_payment_line(lower) or _is_address_line(lower):
        return False
    # Column-header lines ("Description Quantity Rate Total") are never a
    # client name - require at most one header word.
    header_hits = sum(
        1
        for word in _ITEM_HEADER_WORDS
        if re.search(rf"\b{re.escape(word)}\b", lower)
    )
    if header_hits >= 2:
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
        name = _clean_label_value(line)
        if _looks_like_company(name):
            return name

    # The name often sits directly above the invoice reference block - or on
    # the same line just before it ("YARANA TEXTILE MILLS Invoice ref:...").
    ref_match = _INVOICE_NO_RE.search(text)
    if ref_match:
        line_start = text.rfind("\n", 0, ref_match.start()) + 1
        same_line_prefix = _clean_label_value(text[line_start: ref_match.start()])
        if same_line_prefix and _looks_like_company(same_line_prefix):
            return same_line_prefix
        if same_line_prefix and _looks_like_name(same_line_prefix):
            return same_line_prefix

        preceding = text[: ref_match.start()]
        prior_lines = [ln.strip() for ln in preceding.splitlines() if ln.strip()][-3:]
        for line in reversed(prior_lines):
            name = _clean_label_value(line)
            if _looks_like_company(name):
                return name

    # Never fall back to a label/title line ("Invoice") - an empty client is
    # flagged for review and safer than a wrong one.
    for line in lines:
        if _looks_like_name(line):
            return _clean_label_value(line)
    return ""


# ---------------------------------------------------------------------------
# Line item descriptions
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
    "postal code", "project_ref", "project ref",
)

_HEADER_ONLY_DESC = {
    "description", "particulars", "particulars of", "item", "items",
    "item description", "details", "detail", "amount", "amounts", "rate",
    "rates", "qty", "quantity", "unit", "unit price", "unit rate", "value",
    "values", "remarks", "no", "no.", "sl no", "sl.no", "serial no", "product",
    "product name", "code", "uom", "ref", "ref:",
}

_SKIP_DESC_PREFIXES = (
    "invoice no", "invoice number", "invoice #", "invoice ref",
    "invoice date", "invoice dated", "ref:", "page", "tel:", "phone:",
    "fax:", "email:", "e-mail:", "www", "http", "bank:", "swift", "vat",
    "a/c", "date :", "date:", "dated", "total", "sub total", "currency",
    "bill to", "ship to", "client", "customer", "attn", "balance", "no.",
    "no:", "no ",
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


_ITEM_TABLE_HEADER_RE = re.compile(
    r"\b(?:description|particulars)\b.*\b(?:qty|quantity|unit)\b.*"
    r"\b(?:rate|price|amount|total|value)\b",
    re.IGNORECASE,
)
_ITEM_TABLE_HEADER_SIMPLE_RE = re.compile(
    r"\b(?:description|particulars|item)\b.*\b(?:amount|total|value|rate|price)\b",
    re.IGNORECASE,
)
_ITEM_REGION_STOP_FRAGMENTS = (
    "sub total", "subtotal", "grand total", "total amount", "total due",
    "total", "svat", "vat", "tax", "exchange", "payment", "terms", "bank",
    "swift", "signatory", "page", "discount", "advance", "deposit", "road",
    "street", "avenue", "lane", "colombo", "address", "add :", "add:",
    "telephone", "phone", "email", "www", "fax",
)


def _find_item_region(lines: Sequence[str]) -> int:
    """Return the index of the first line that may hold line items.

    When a line-item table header (e.g. "Description Quantity Rate Total")
    is found, only the lines *after* it are parsed as items - supplier
    address/reference blocks printed above the table can never leak into
    the line items. Returns 0 when no header is found (free-form layout).
    """
    for index, line in enumerate(lines):
        lower = line.lower()
        if _ITEM_TABLE_HEADER_RE.search(lower) or _ITEM_TABLE_HEADER_SIMPLE_RE.search(lower):
            return index + 1
    return 0


def _is_region_stop_line(line: str) -> bool:
    lower = line.lower()
    return any(fragment in lower for fragment in _ITEM_REGION_STOP_FRAGMENTS)


def _looks_like_item_text(line: str) -> bool:
    """True when a non-parseable table line is probably a wrapped continuation
    of the next item's description (e.g. a long item split over two lines)."""
    stripped = line.strip(" -•*()[]:.,;\"'")
    if len(stripped) < 3:
        return False
    if not re.search(r"[A-Za-z]{2,}", stripped):
        return False
    return not stripped.lower().startswith(_SKIP_DESC_PREFIXES)


def _parse_item_line(
    line: str, default_currency: str, table_mode: bool = False
) -> Optional[InvoiceLine]:
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

        # A bare integer with no decimals is rarely money - it is usually an
        # area/postal code, index or quantity ("Colombo 08", "page 4",
        # "Project_ref: PRJ 866806"). Only accept it when a currency token
        # sits right next to it, it is a small number, or we are inside a
        # recognised item table where the last column is the amount.
        is_bare_integer = "." not in raw_amount and "," not in raw_amount
        if is_bare_integer:
            next_to_currency = bool(
                (i - 1 >= 0 and normalize_currency(tokens[i - 1]))
                or (i + 1 < len(tokens) and normalize_currency(tokens[i + 1]))
            )
            if not next_to_currency and (not table_mode or amount < 100):
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

        description = " ".join(desc_tokens).strip(" -•*:.,;\"'")
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
    """Parse plausible line items out of ``text`` in order.

    When a line-item table header is found, only the region after it is
    scanned, up to the first totals / payment / address line - so supplier
    address blocks above the table or bank details below it never produce
    fake items. Without a header the whole text is scanned (free-form
    layout), where trailing bare integers of 100 or more are rejected as
    amounts (they are usually postal codes, project refs or phone numbers).
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    region_start = _find_item_region(lines)
    table_mode = region_start > 0

    items: List[InvoiceLine] = []
    order = 0
    pending: List[str] = []
    for index, raw_line in enumerate(lines):
        if index < region_start:
            continue
        if table_mode and _is_region_stop_line(raw_line):
            break
        item = _parse_item_line(raw_line, default_currency, table_mode=table_mode)
        if item is None:
            if table_mode and _looks_like_item_text(raw_line):
                pending.append(raw_line.strip())
            continue
        if pending:
            item.description = " ".join(pending) + " " + item.description
            pending = []
        order += 1
        item.line_order = order
        items.append(item)
    return items


def extract_descriptions(text: str, default_currency: str = "") -> List[str]:
    """Return the invoice charge descriptions only, in document order.

    Financial summary rows (Sub Total, VAT, Total, exchange rate, bank and
    payment terms) are already excluded by the line-item parser, so only the
    actual charge descriptions remain - e.g. ``["TC Charges ... 01/02/2026",
    "COI Charges ... 01/02/2026"]``.
    """
    return [item.description for item in extract_line_items(text, default_currency)]


# ---------------------------------------------------------------------------
# Invoice total extraction
#
# Layouts vary between suppliers, so we scan every line for a *total-like*
# label, ignore lines that are clearly subtotals / tax / VAT / exchange-rate
# rows, then rank the remaining candidates:
#   - the most explicit label ("grand total" > "invoice total" > "total")
#   - among equal labels, the LAST occurrence in the document wins, because
#     the final payable amount usually appears at the bottom of the invoice.
# ---------------------------------------------------------------------------

_TOTAL_EXCLUDED = (
    "sub total", "subtotal", "tax amount", "tax", "vat", "excise", "cess",
    "levy", "exchange rate", "exch rate", "discount", "less:", "advance",
    "deposit", "paid", "round off", "quantity", "qty",
)

_TOTAL_PATTERNS = (
    (re.compile(r"\bgrand\s*total\b"), 10),
    (re.compile(r"\binvoice\s*total\b"), 9),
    (re.compile(r"\btotal\s*amount\b"), 9),
    (re.compile(r"\btotal\s*payable\b"), 9),
    (re.compile(r"\bamount\s*(?:due|owing|payable)\b"), 9),
    (re.compile(r"\bbalance\s*due\b"), 8),
    (re.compile(r"\bnet\s*amount\b"), 8),
    (re.compile(r"\bbalance\b"), 7),
    (re.compile(r"\btotal\b"), 5),
)

# On SVAT / suspended-tax invoices the final payable is the supply value
# *plus* the SVAT, i.e. "Total value of the supply with SVAT". Plain "Total"
# lines on such invoices are usually just the line-item subtotal written out
# in words, so this label must outrank them.
_TOTAL_SVAT_RE = re.compile(
    r"\btotal\s+value\s+of\s+the\s+supply\b",
    re.IGNORECASE,
)

# Labels that identify the *subtotal before VAT/tax*. The final payable total
# ("grand total", "invoice total", plain "total") must never be used here.
_SUBTOTAL_EXCLUDED = (
    "grand total", "invoice total", "total amount", "total payable",
    "amount due", "amount owing", "balance due", "vat", "tax amount", "tax",
    "excise", "cess", "exchange rate", "exch rate", "payment terms",
    "payment instructions", "amount in words", "words", "less: advance",
    "less advance", "advance paid", "deposit paid",
)

_SUBTOTAL_PATTERNS = (
    (re.compile(r"\bsub\s*[- ]?total\b"), 10),  # Sub Total / Subtotal / Sub-total
    (re.compile(r"\bnet\s*total\b"), 8),
    (re.compile(r"\bnet\s*amount\b"), 8),
    (re.compile(r"\btotal\s*before\s*(?:tax|vat)\b"), 7),
)

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "million": 1000000,
}


def _parse_small_number(tokens: List[str]) -> int:
    """Turn a short phrase like 'ninety five' into 95 (used for cents)."""
    values = [
        _WORD_NUMBERS[token]
        for token in tokens
        if token in _WORD_NUMBERS and _WORD_NUMBERS[token] < 100
    ]
    if not values:
        return 0
    total = values[0]
    for value in values[1:]:
        total += value
    return total


def _words_to_number(text: str) -> Optional[float]:
    """Convert spelled-out amounts ('One Thousand Three Hundred Forty Five')
    into a number. Handles a trailing 'x cent(s)' phrase or an 'and NN/100'
    fraction for the decimals."""
    words = re.findall(r"[a-z]+", text.lower())
    if not any(word in _WORD_NUMBERS for word in words):
        return None

    cents = 0
    total = 0
    track = 0
    found = False

    fraction = re.search(r"\b(?:and\s+)?(\d{1,3})\s*/\s*(\d{1,3})\b", text.lower())
    if fraction and int(fraction.group(2)) > 0:
        cents = round(int(fraction.group(1)) * 100 / int(fraction.group(2)))
        found = True

    for i, word in enumerate(words):
        if word.startswith("cent"):  # 'cents' marks the decimal phrase
            cents = _parse_small_number(words[max(0, i - 3):i])
            continue
        if word in ("hundred",):
            track = track * 100 if track else 100
        elif word in ("thousand", "million"):
            multiplier = 1000 if word == "thousand" else 1000000
            track = track * multiplier if track else multiplier
            total += track
            track = 0
        elif word in _WORD_NUMBERS:
            track += _WORD_NUMBERS[word]
            found = True

    total += track
    if not found:
        return None
    return round(total + cents / 100, 2)


def _extract_numeric_from_line(line: str) -> Optional[float]:
    """Return the last plausible money amount found on a single line."""
    amounts = []
    for token in re.findall(r"-?[\d,]+(?:\.\d{1,2})?", line):
        value = clean_amount(token)
        if value is None:
            continue
        # Ignore year-like numbers such as '2026'.
        if value.is_integer() and 1900 <= value <= 2100:
            continue
        amounts.append(value)
    return amounts[-1] if amounts else None


def _total_candidate_amount(lines: List[str], index: int) -> Optional[float]:
    """Find the amount belonging to a total label line.

    Looks at the label line first (numeric, then spelled-out words), then at
    up to two following lines (some invoices put the number underneath the
    label, e.g. after a spelled-out 'Total : Euro One Thousand ...').
    """
    for offset in range(3):
        line_index = index + offset
        if line_index >= len(lines):
            break
        # Spelled-out amounts win over stray numbers (e.g. a '50/100' cents
        # fraction would otherwise be read as 100).
        amount = _words_to_number(lines[line_index])
        if amount is not None:
            return amount
        amount = _extract_numeric_from_line(lines[line_index])
        if amount is not None:
            return amount
    return None


def _total_label_rank(lower_line: str) -> Optional[int]:
    """Return the priority of the total label found on a line, or None."""
    for pattern, rank in _TOTAL_PATTERNS:
        if pattern.search(lower_line):
            return rank
    return None


def _subtotal_label_rank(lower_line: str) -> Optional[int]:
    """Return the priority of the subtotal label found on a line, or None."""
    for pattern, rank in _SUBTOTAL_PATTERNS:
        if pattern.search(lower_line):
            return rank
    return None


def extract_sub_total(text: str) -> Optional[float]:
    """Return the invoice subtotal (value before VAT/tax), or ``None``.

    Looks for labels such as ``Sub Total``/``Subtotal``/``Net Amount`` and
    never returns a VAT/tax figure, the grand/final total or an exchange rate.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None

    candidates: List[tuple] = []
    for index, line in enumerate(lines):
        lower = line.lower()
        if any(keyword in lower for keyword in _SUBTOTAL_EXCLUDED):
            continue
        rank = _subtotal_label_rank(lower)
        if rank is None:
            continue
        amount = _total_candidate_amount(lines, index)
        if amount is not None:
            candidates.append((rank, index, amount))

    if not candidates:
        return None

    best_rank = max(candidate[0] for candidate in candidates)
    best_candidates = [c for c in candidates if c[0] == best_rank]
    chosen = max(best_candidates, key=lambda c: c[1])
    return round(chosen[2], 2)


def extract_total_amount(text: str) -> Optional[float]:
    """Return the most likely final invoice payable amount, or ``None``.

    Never returns a subtotal, tax or VAT figure - those lines are excluded
    before ranking. When several total-like values exist, the value with the
    most explicit label and the latest position in the document wins. On
    SVAT / suspended-tax invoices the "Total value of the supply with SVAT"
    line is the final payable and outranks plain "Total" lines.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None

    candidates: List[tuple] = []
    for index, line in enumerate(lines):
        lower = line.lower()
        is_svat_total = bool(_TOTAL_SVAT_RE.search(lower))
        if not is_svat_total and any(keyword in lower for keyword in _TOTAL_EXCLUDED):
            continue
        rank = _total_label_rank(lower)
        if rank is None:
            continue
        if is_svat_total:
            rank = max(rank, 11)
        amount = _total_candidate_amount(lines, index)
        if amount is not None:
            candidates.append((rank, index, amount))

    if not candidates:
        return None

    best_rank = max(candidate[0] for candidate in candidates)
    best_candidates = [c for c in candidates if c[0] == best_rank]
    chosen = max(best_candidates, key=lambda c: c[1])
    return round(chosen[2], 2)


# Backwards-compatible alias used by earlier tests/callers.
extract_total = extract_total_amount


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def parse_invoice(
    text: str,
    file_name: str = "",
    extraction_method: str = "",
    hint: Optional[Dict] = None,
) -> ParsedInvoice:
    """Parse invoice text into an invoice-level :class:`ParsedInvoice`.

    ``hint`` is an optional dict (e.g. produced by Azure Document Intelligence)
    whose non-empty values take precedence over the rule-based extraction.

    ``sub_total`` and ``total_amount`` are never taken from individual line
    item amounts - they come from the structured hint or the explicit
    Sub-total / Total labels in the document, or are left ``None`` so the
    record is flagged for review.
    """
    hint = hint or {}

    invoice_no = (
        str(hint.get("invoice_no") or "").strip() or extract_invoice_number(text)
    )
    client = (
        str(hint.get("client") or hint.get("client_name") or "").strip()
        or extract_client_name(text)
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

    # Descriptions stay a list internally; only joined with newlines at
    # display/export time so the user can edit each invoice's cell freely.
    descriptions = [line.description for line in lines]

    # Sub total priority: 1. structured hint, 2. explicit Sub Total label.
    # Use "is not None": a genuine 0.0 (credit note / zero-rated row) is a
    # valid amount and must not be treated as falsy.
    hint_sub_total = clean_amount(hint.get("sub_total"))
    sub_total = hint_sub_total if hint_sub_total is not None else extract_sub_total(text)

    # Total amount priority: 1. structured hint, 2. explicit total label.
    # Never fall back to summing line items - that could produce a value that
    # ignores VAT and would be silently wrong.
    hint_total = clean_amount(hint.get("total_amount"))
    total_amount = (
        hint_total if hint_total is not None else extract_total_amount(text)
    )

    warnings: List[str] = []
    if not client:
        warnings.append("Missing client name")
    if not invoice_no:
        warnings.append("Missing invoice number")
    if not invoice_date:
        warnings.append("Missing invoice date")
    if not currency:
        warnings.append("Missing currency")
    if not descriptions:
        warnings.append("No line item descriptions detected")
    if sub_total is None:
        warnings.append("Sub total not found")
    if total_amount is None:
        warnings.append("Missing total amount")

    extraction_status = "processed" if total_amount is not None else "needs_review"

    return ParsedInvoice(
        client=client,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        currency=currency,
        descriptions=descriptions,
        sub_total=sub_total,
        total_amount=total_amount,
        source_file_name=file_name,
        raw_text=text,
        extraction_method=extraction_method,
        extraction_status=extraction_status,
        warnings=warnings,
    )


EXPORT_COLUMNS = (
    "Client",
    "Invoice No",
    "Invoice Date",
    "Description",
    "Sub total",
    "Total amount",
    "Currency",
)


def invoice_to_row(invoice: ParsedInvoice) -> Dict[str, object]:
    """Flatten one parsed invoice into a single exportable/editable row.

    Descriptions are joined with real newlines (Excel renders them on
    separate lines inside the same cell). One row per invoice.
    """
    return {
        "Client": invoice.client,
        "Invoice No": invoice.invoice_no,
        "Invoice Date": invoice.invoice_date,
        "Description": invoice.description,
        "Sub total": invoice.sub_total if invoice.sub_total is not None else "",
        "Total amount": invoice.total_amount if invoice.total_amount is not None else "",
        "Currency": invoice.currency,
    }
