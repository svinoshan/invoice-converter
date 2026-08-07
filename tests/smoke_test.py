"""Smoke test suite for the invoice converter.

Run from the project root:

    .venv/bin/python tests/smoke_test.py

Covers the parser, validators, Supabase service (mocked client), config
resolution, UI helpers, processing pipeline, schema and page structure.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import io  # noqa: E402

from services import invoice_parser, processing, supabase_service, validators  # noqa: E402
from utils import config as config_module  # noqa: E402
from utils import ui_helpers  # noqa: E402
from utils.ui_helpers import row_to_record  # noqa: E402
from services.supabase_service import (  # noqa: E402
    _friendly_error,
    _normalize_record,
    _to_iso_date,
    _to_optional_float,
)
from services.validators import validate_row, validate_rows  # noqa: E402

# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

CHECKS = []


def check(label: str, fn) -> None:
    CHECKS.append((label, fn))


def run_all() -> int:
    passed = 0
    failed = 0
    for label, fn in CHECKS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report every failure
            failed += 1
            print(f"FAIL {label}: {exc}")
        else:
            passed += 1
            print(f"  ok  {label}")
    print(f"{passed} passed, {failed} failed, {len(CHECKS)} total")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# 1. Parser: currency
# ---------------------------------------------------------------------------


def _currency_normalize():
    assert invoice_parser.normalize_currency("€") == "Euro"
    assert invoice_parser.normalize_currency("EUR") == "Euro"
    assert invoice_parser.normalize_currency("euros") == "Euro"
    assert invoice_parser.normalize_currency("Rs.") == "LKR"
    assert invoice_parser.normalize_currency("rupees") == "LKR"
    assert invoice_parser.normalize_currency("us$") == "USD"
    assert invoice_parser.normalize_currency("$") == "USD"
    assert invoice_parser.normalize_currency("£") == "GBP"
    assert invoice_parser.normalize_currency("garbage!") == ""
    assert invoice_parser.normalize_currency(None) == ""


check("parser: normalize_currency maps symbols/codes", _currency_normalize)


def _currency_extract():
    text = "Amounts in Euro and euros and more Euro.\nAlso EUR here."
    assert invoice_parser.extract_currency(text) == "Euro"
    assert invoice_parser.extract_currency("Nothing here 123") == ""
    assert invoice_parser.extract_currency("") == ""


check("parser: extract_currency picks the majority", _currency_extract)


# ---------------------------------------------------------------------------
# 2. Parser: amounts and dates
# ---------------------------------------------------------------------------


def _clean_amount():
    assert invoice_parser.clean_amount("1,140.00") == 1140.0
    assert invoice_parser.clean_amount("1,345.20") == 1345.2
    assert invoice_parser.clean_amount("(53.10)") == -53.1
    assert invoice_parser.clean_amount("Rs 1,250.00") == 1250.0
    assert invoice_parser.clean_amount("$ 1,000.50") == 1000.5
    assert invoice_parser.clean_amount("€ 99") == 99.0
    assert invoice_parser.clean_amount(42) == 42.0
    assert invoice_parser.clean_amount(True) is None
    assert invoice_parser.clean_amount("") is None
    assert invoice_parser.clean_amount("abc") is None
    assert invoice_parser.clean_amount(None) is None


check("parser: clean_amount handles separators/currency/negatives", _clean_amount)


def _normalize_date():
    assert invoice_parser.normalize_date("2026-06-08") == "8-Jun-26"
    assert invoice_parser.normalize_date("08/06/2026") == "8-Jun-26"
    assert invoice_parser.normalize_date("8/6/26") == "8-Jun-26"
    assert invoice_parser.normalize_date("8-Jun-26") == "8-Jun-26"
    assert invoice_parser.normalize_date("1-Jan-25") == "1-Jan-25"
    assert invoice_parser.normalize_date("") == ""
    assert invoice_parser.normalize_date("not a date") == ""


check("parser: normalize_date canonical d-mmm-yy", _normalize_date)


def _invoice_date():
    text = "Invoice Date: 8-Jun-26\nInvoice No: INV-1"
    assert invoice_parser.extract_invoice_date(text) == "8-Jun-26"
    assert invoice_parser.extract_invoice_date("") == ""


check("parser: extract_invoice_date finds labelled date", _invoice_date)


# ---------------------------------------------------------------------------
# 3. Parser: invoice number, client, descriptions, totals
# ---------------------------------------------------------------------------


def _invoice_number():
    assert invoice_parser.extract_invoice_number("Invoice No: INV-2026-001") == "INV-2026-001"
    assert invoice_parser.extract_invoice_number("Invoice Number : 12345") == "12345"
    assert invoice_parser.extract_invoice_number("Ref: INV/2026/001") == "INV/2026/001"
    assert invoice_parser.extract_invoice_number("No invoice here") == ""


check("parser: extract_invoice_number labelled + bare refs", _invoice_number)


def _client_name():
    text = "Client: Acme Trading Ltd\nInvoice No: INV-1"
    assert invoice_parser.extract_client_name(text) == "Acme Trading Ltd"
    assert invoice_parser.extract_client_name("") == ""


check("parser: extract_client_name finds company suffix", _client_name)


_SAMPLE_TEXT = (
    "Client: Acme Trading Ltd\n"
    "Invoice No: INV-2026-001\n"
    "Invoice Date: 8-Jun-26\n"
    "TC Charges for the period 01/02/2026 to 29/02/2026    1,140.00\n"
    "COI Charges 01/03/2026    205.20\n"
    "Sub Total                    1,345.20\n"
    "VAT 0%\n"
    "Grand Total: Euro 1,345.20\n"
)


def _descriptions():
    descs = invoice_parser.extract_descriptions(_SAMPLE_TEXT)
    assert descs == [
        "TC Charges for the period 01/02/2026 to 29/02/2026",
        "COI Charges 01/03/2026",
    ], descs


check("parser: extract_descriptions excludes summary rows", _descriptions)


_YVONNE_SVAT_TEXT = (
    "SUSPENDED TAX INVOICE\n"
    "Yvonne Lanka (Pvt) Ltd Invoice ref: LKA/CUC/SVAT/2020/09/0402\n"
    "Nurani Industrial Park, Waikkala Project_ref: PRJ 866806\n"
    "Postal code: 61170 Date: 30-09-2020\n"
    "Wennappuwa\n"
    "SRI LANKA\n"
    "(+94) 71 425 2038,\n"
    "piyalns.lk@yvonnegroup.com\n"
    "VAT Registration no.. 100895579-7000\n"
    "SVAT Registration no.. 11584\n"
    "Description Quantity Rate Total :\n"
    "Invoice for Higg FEM Verification 1 600 600.00\n"
    "USD six hundred only Total : USD 600.00\n"
    "The LKR value of the SVAT amount is 8709.12 SVAT @ 8.00% 48.00\n"
    "Exchange Rate: 1 USD = 181.4400 LKR\n"
    "Total value of the supply with SVAT USD 648.00\n"
    "Payment Terms:\n"
    "60 days from the date of invoice.\n"
    "Bank Details:\n"
    "Commercial Bank Of Ceylon PLC\n"
    "A/C No: 8010003244\n"
    "Swift/Bic No. CCEYLKLX\n"
    "Reid Avenue Branch\n"
    "ADD: No.104, Reid Avenue, Colombo 04.\n"
)


def _yvonnie_address_block():
    items = invoice_parser.extract_line_items(_YVONNE_SVAT_TEXT)
    assert [i.description for i in items] == ["Invoice for Higg FEM Verification"], items
    assert items[0].amount == 600.0
    descs = invoice_parser.extract_descriptions(_YVONNE_SVAT_TEXT)
    assert descs == ["Invoice for Higg FEM Verification"], descs
    parsed = invoice_parser.parse_invoice(_YVONNE_SVAT_TEXT)
    assert parsed.invoice_date == "30-Sep-20", parsed.invoice_date
    assert parsed.invoice_no == "LKA/CUC/SVAT/2020/09/0402"
    assert parsed.client == "Yvonne Lanka (Pvt) Ltd"
    assert parsed.total_amount == 648.0, parsed.total_amount


check("parser: supplier address block never leaks into items", _yvonnie_address_block)


_TROPICAL_WRAPPED_TEXT = (
    "SUSPENDED TAX INVOICE\n"
    "Tropical Health Food (Pvt) Ltd. Invoice ref: LKA/CUC/SVAT/2020/12/0642\n"
    "Heraliyawela Industrial Park Project_ref: PRJ 004161\n"
    "Malkaduwawa , Kurunegala Date: 18-12-2020\n"
    "SRI LANKA\n"
    "0094-37-2224308\n"
    "info@thf.lk\n"
    "VAT Registration no.. 114128007-7000\n"
    "SVAT Registration no.. SVAT004620\n"
    "Description Quantity Rate Total :\n"
    "Inspection & Certification Fee for EEC for 2020 : 3 Days @ Euro 500/Day (Offer\n"
    "Ref. No : : CUSL-ORG-2020-190-APR 147667)(Additional Day) 3 500 1,500.00\n"
    "EUR one thousand five hundred only Total : EUR 1,500.00\n"
    "The LKR value of the SVAT amount is 26905.20 SVAT @ 8.00% 120.00\n"
    "Exchange Rate: 1 EUR = 224.2100 LKR\n"
    "Total value of the supply with SVAT EUR 1,620.00\n"
    "Payment Terms:\n"
    "All bank charges are on Account of the Customer\n"
    "Kindly include the invoice number in the Telegraphic Transfer Instructions\n"
    "The Gross Invoice value is payable within 14days from the date of invoice\n"
    "Payment Instructions-\n"
    "Commercial Bank Of Ceylon PLC\n"
    "Reid Avenue Branch Control Union Inspections (Pvt) Ltd\n"
    "ADD: No.104, Reid Avenue,\n"
    "Colombo 04.\n"
    "A/C No: 8010003245 Account type:FEEA (EUR) SAVINGS\n"
    "Swift/Bic No. CCEYLKLX\n"
    "SVAT No. :SVAT004104 Authorised Signatory\n"
    "VAT No. :114462322-7000\n"
    "Page: 1 of 1\n"
)


def _tropical_wrapped_line():
    descs = invoice_parser.extract_descriptions(_TROPICAL_WRAPPED_TEXT)
    assert len(descs) == 1, descs
    assert descs[0] == (
        "Inspection & Certification Fee for EEC for 2020 : 3 Days @ "
        "Euro 500/Day (Offer Ref. No : : CUSL-ORG-2020-190-APR 147667)"
        "(Additional Day)"
    ), descs[0]
    parsed = invoice_parser.parse_invoice(_TROPICAL_WRAPPED_TEXT)
    assert parsed.total_amount == 1620.0, parsed.total_amount
    assert parsed.client == "Tropical Health Food (Pvt) Ltd."
    assert parsed.invoice_date == "18-Dec-20", parsed.invoice_date


check("parser: wrapped item description joins continuation lines", _tropical_wrapped_line)


_YARANA_TEXT = (
    "Invoice\n"
    "YARANA TEXTILE MILLS Invoice ref:LKA/CUC/2026/06/1237\n"
    "SUITE 304, 3RD FLOOR, UNI-TOWER, Date :18-Jun-26\n"
    "ADJ. COTTON\n"
    "EXCHANGE BUILDING,\n"
    "I.I CHUNDRIGAR ROAD,\n"
    "Karachi,South ,Postal code: 74000\n"
    "Postal Code :\n"
    "Pakistan\n"
    "92-21-3246111\n"
    "yaranatextile786@gmail.com\n"
    "Description Quantity Rate Total\n"
    "TC Charges - 01/02/2026-30/04/2026 1 105.00 € 105.00\n"
    "Total Euro 105.00\n"
    "One Hundred Five Euro Only\n"
    "Payment Terms:\n"
    "All bank charges are on Account of the Customer\n"
    "Kindly include the invoice number in the Telegraphic Transfer Instructions\n"
    "The Gross Invoice value is payable within 14days from the date of invoice\n"
    "Payment Instructions:\n"
    "Commercial Bank of Ceylon PLC\n"
    "Reid Avenue Branch\n"
    "ADD: No.104, Reid Avenue,\n"
    "Colombo 04.\n"
    "Control Union Inspections (Pvt) Ltd\n"
    "A/C No: 8010003245\n"
    "Account type: FEEA (EUR) SAVINGS\n"
    "Swift/Bic No. CCEYLKLX\n"
    "Authorised Signatory\n"
)


def _yarana_client_without_suffix():
    parsed = invoice_parser.parse_invoice(_YARANA_TEXT)
    assert parsed.client == "YARANA TEXTILE MILLS", parsed.client
    assert parsed.invoice_no == "LKA/CUC/2026/06/1237"
    assert parsed.invoice_date == "18-Jun-26", parsed.invoice_date
    assert parsed.total_amount == 105.0, parsed.total_amount
    assert parsed.currency == "Euro", parsed.currency


check("parser: client without legal suffix + bank block ignored", _yarana_client_without_suffix)


def _review_fix_dates():
    assert invoice_parser.normalize_date("06/15/2026") == "15-Jun-26"
    assert invoice_parser.normalize_date("08/06/2026") == "8-Jun-26"
    assert invoice_parser.normalize_date("8") == ""
    assert invoice_parser.extract_invoice_date("Invoice Date : 8 June 2026\n") == "8-Jun-26"
    assert invoice_parser.extract_invoice_date("Invoice Date : 18 Jun 2026\n") == "18-Jun-26"


check("review fix: MM/DD fallback + 3-token dates", _review_fix_dates)


def _review_fix_invoice_no():
    assert invoice_parser.extract_invoice_number("Invoice No: TC 2026 06 0012") == "TC 2026 06 0012"
    assert invoice_parser.extract_invoice_number("Invoice No: INV-123 Date: 01-Jun-26") == "INV-123"
    assert invoice_parser.extract_invoice_number("Invoice No: LKA/CUC/SVAT/2020/09/0402") == "LKA/CUC/SVAT/2020/09/0402"


check("review fix: multi-segment refs + inline labels", _review_fix_invoice_no)


def _review_fix_client_and_currency():
    text = (
        "Invoice\n"
        "M. Perera, Accounts Department\n"
        "M/s Grand Textiles (Pvt) Ltd\n"
        "Invoice ref: LKA/CUC/2026/01/0001\n"
    )
    assert invoice_parser.extract_client_name(text) == "Grand Textiles (Pvt) Ltd"
    assert invoice_parser.normalize_currency("A$") == "AUD"
    assert invoice_parser.normalize_currency("C$") == "CAD"
    assert invoice_parser.normalize_currency("S$") == "SGD"
    assert invoice_parser.normalize_currency("$") == "USD"


check("review fix: M. ignored, M/s works, dollar variants", _review_fix_client_and_currency)


def _review_fix_totals():
    assert invoice_parser.extract_total_amount("Total (Including Tax) 1,234.00\nPayment Terms: x") == 1234.0
    assert invoice_parser.extract_sub_total("Sub Total (before VAT adjustment) 500.00\nVAT 100.00") == 500.0
    assert invoice_parser.extract_total_amount("Total:\nVAT 15% 750.00\nTotal amount 1,000.00") == 1000.0
    assert invoice_parser.clean_amount("5,000.00 LKR") == 5000.0
    assert invoice_parser.clean_amount("1,250.00 rs.") == 1250.0


check("review fix: tax-inclusive totals, lookahead, trailing currency", _review_fix_totals)


def _review_fix_region_stop():
    text = (
        "Invoice\n"
        "ACME Sizing Mills\n"
        "Invoice ref: LKA/CUC/2026/09/0011\n"
        "Description Quantity Rate Total\n"
        "Transport Colombo Road to Kandy 1 5,000.00 5,000.00\n"
        "Total 5,000.00\n"
        "Payment Terms: 30 days\n"
    )
    assert invoice_parser.extract_descriptions(text) == ["Transport Colombo Road to Kandy"]


check("review fix: road item not stopped by region guard", _review_fix_region_stop)


def _misspelled_words_total():
    text = (
        "TAX INVOICE\n"
        "Sri Lanka Natural Spice & Herbal Products Invoice ref: LKA/CUC/2024/04/0122\n"
        "Description Quantity Rate Total :\n"
        "TC Charges 01/11/2023 - 31/12/2023 1 270 270.00\n"
        "Sub total EUR 270.00\n"
        "Tax amount 48.60\n"
        "EUR Three Hundred and Eightteen and Sixty cents only Total : EUR 318.60\n"
    )
    assert invoice_parser.extract_total_amount(text) == 318.6


check("total: typo words but numeric on line wins", _misspelled_words_total)


def _review2_client_fallback():
    text = (
        "TAX INVOICE\n"
        "Bill To: John Doe\n"
        "Acme Spice Mills Pvt Ltd\n"
        "Invoice ref: LKA/CUC/2024/04/0122\n"
    )
    assert invoice_parser.extract_client_name(text) == "Acme Spice Mills Pvt Ltd"
    text_no_suffix = "TAX INVOICE\nBill To: John Doe\nYARANA TEXTILE MILLS\nInvoice ref: LKA/2\n"
    assert invoice_parser.extract_client_name(text_no_suffix) == "YARANA TEXTILE MILLS"
    assert invoice_parser.extract_client_name("Invoice\nBill To: John Doe\nTotal 500.00\n") == ""


check("review2: customer labels never leak into client", _review2_client_fallback)


def _review2_amounts():
    assert invoice_parser._is_amount_token("2,000")
    assert not invoice_parser._is_amount_token("2000")
    items = invoice_parser.extract_line_items("Consultation Fee 2,000\n", "USD")
    assert [(i.description, i.amount) for i in items] == [("Consultation Fee", 2000.0)]
    items = invoice_parser.extract_line_items(
        "Description Quantity Rate Total\nService Fee 1 50 50\n", "USD"
    )
    assert [(i.description, i.amount) for i in items] == [("Service Fee", 50.0)]


check("review2: comma'd 2,000 is money, bare 2000 is a year, table small ints", _review2_amounts)


def _review2_same_line_tax():
    assert invoice_parser.extract_sub_total("Sub Total 1,000.00 VAT 150.00\n") == 1000.0
    assert invoice_parser.extract_total_amount("Rate: 135.00 Total: 2,000.00\n") == 2000.0


check("review2: totals never cross tax columns on the same line", _review2_same_line_tax)


def _review2_dates():
    assert invoice_parser.extract_invoice_date("Date: August 15, 2026\n") == "15-Aug-26"
    assert invoice_parser.extract_invoice_date("Date : 15-Aug\n") == ""
    assert invoice_parser.extract_invoice_date("Date : 2026\n") == ""
    assert invoice_parser.extract_invoice_date("Invoice Date : 8 June 2026\n") == "8-Jun-26"


check("review2: dates need explicit month and year", _review2_dates)


def _review2_invoice_no():
    assert invoice_parser.extract_invoice_number("Invoice: 12345\n") == "12345"
    assert invoice_parser.extract_invoice_number("Invoice: 12\n") == "12"
    assert invoice_parser.extract_invoice_number("Invoice 15 Aug 2026\n") == ""
    assert invoice_parser.extract_invoice_number("No invoice here\n") == ""
    assert invoice_parser.extract_invoice_number("Invoice No: 26-04-2024\n") == ""
    assert invoice_parser.extract_invoice_number(
        "TAX INVOICE\nSri Lanka Spice Pvt Ltd\nInvoice No: LKA/CUC/2024/04/0122\n"
    ) == "LKA/CUC/2024/04/0122"
    assert invoice_parser.extract_invoice_number("INV-001\n") == "INV-001"
    assert invoice_parser.extract_invoice_number("INV/2026\n") == "INV/2026"
    assert invoice_parser.extract_invoice_number("NET\nVAT\nPAY\n") == ""


check("review2: plain Invoice: label, bare one-segment refs, date guards", _review2_invoice_no)


def _review2_net_amount():
    assert invoice_parser.extract_total_amount(
        "Net Amount 10,000.00\nVAT 1,500.00\nTotal 11,500.00\n"
    ) == 11500.0
    assert invoice_parser.extract_total_amount("Net Amount 1,000.00\n") == 1000.0
    assert invoice_parser.extract_sub_total("Net Amount 10,000.00\n") == 10000.0


check("review2: net amount never outranks total", _review2_net_amount)


def _review2_excluded_words():
    assert invoice_parser.extract_total_amount(
        "Total Amount Paid 5,000.00\n"
    ) == 5000.0
    assert invoice_parser.extract_total_amount(
        "Total (excl. tax) 1,000.00\nVAT 150.00\nTotal 1,150.00\n"
    ) == 1150.0


check("review2: word-boundary total exclusions", _review2_excluded_words)


def _review2_region_stop():
    text = (
        "Description Quantity Rate Total\n"
        "Total cleaning of warehouse - 5 days 500.00 500.00\n"
        "Painting works 2 250.00 500.00\n"
        "Foreign Exchange Consulting 2 250 500.00\n"
    )
    assert [(i.description, i.amount) for i in invoice_parser.extract_line_items(text, "USD")] == [
        ("Painting works", 500.0),
        ("Foreign Exchange Consulting", 500.0),
    ]
    stop_text = (
        "Description Quantity Rate Total\n"
        "Item A 1 100 100.00\n"
        "Total value of the supply with SVAT USD 648.00\n"
        "Item B 1 200 200.00\n"
    )
    assert invoice_parser.extract_descriptions(stop_text, "USD") == ["Item A"]


check("review2: prose with total/exchange survives, summary rows still stop", _review2_region_stop)


_PIERIS_TEXT = (
    "SUSPENDED TAX INVOICE\n"
    "Richard Pieris Natural Foams Ltd. Invoice ref: LKA/CUC/SVAT/2021/01/0689\n"
    "Phase 1 - Export processing zone Project_ref: PRJ 821802\n"
    "Postal code: 61170 Date: 21-01-2021\n"
    "Description Quantity Rate Total :\n"
    "Inspection of mattresses 1 500 500.00\n"
    "Total value of the supply with SVAT USD 540.00\n"
)


def _pieris_long_header_line():
    parsed = invoice_parser.parse_invoice(_PIERIS_TEXT)
    assert parsed.client == "Richard Pieris Natural Foams Ltd.", parsed.client
    assert parsed.invoice_no == "LKA/CUC/SVAT/2021/01/0689"
    assert parsed.invoice_date == "21-Jan-21", parsed.invoice_date
    assert parsed.total_amount == 540.0, parsed.total_amount


check("parser: long name+ref line beats address line", _pieris_long_header_line)


_HEXAGON_TEXT = (
    "TAX INVOICE\n"
    "Hexagon Asia (Pvt) Ltd. Invoice ref: LKA/CUC/2021/03/1051\n"
    "T2/34-B2, On Three20, Union Place, Project_ref: PRJ 869168\n"
    "Colombo 02 Date: 24-03-2021\n"
    "SRI LANKA\n"
    "mahesh@hexagonasia.com\n"
    "Description Quantity Rate Total :\n"
    "Fee for GOLS Transaction Certificates for 1st July 2020 to 31st Dec 2020 66 30 1,980.00\n"
    "Sub total EUR 1,980.00\n"
    "Tax amount 158.40\n"
    "1,980.00 VAT 158.40\n"
    "158.40\n"
    "EUR two thousand one hundred thirty eight and forty cents only Total : EUR 2,138.40\n"
    "The LKR value of the VAT amount is 36802.66\n"
    "Payment Terms:\n"
    "All bank charges are on Account of the Customer\n"
    "Kindly include the invoice number in the Telegraphic Transfer Instructions\n"
    "The Gross Invoice value is payable within 14days from the date of invoice\n"
    "Payment Instructions-\n"
    "Commercial Bank Of Ceylon PLC Control Union Inspections (Pvt) Ltd\n"
    "Reid Avenue Branch\n"
    "ADD: No.104, Reid Avenue,\n"
    "Colombo 04.\n"
    "A/C No: 8010003245 Account type:FEEA (EUR) SAVINGS\n"
    "Swift/Bic No. CCEYLKLX Authorised Signatory\n"
    "VAT No. :114462322-7000\n"
    "Page: 1 of 3\n"
    "TC/ITC Annex\n"
)


def _hexagon_subtotal_and_cents_total():
    parsed = invoice_parser.parse_invoice(_HEXAGON_TEXT)
    assert parsed.client == "Hexagon Asia (Pvt) Ltd.", parsed.client
    assert parsed.invoice_no == "LKA/CUC/2021/03/1051"
    assert parsed.invoice_date == "24-Mar-21", parsed.invoice_date
    assert parsed.sub_total == 1980.0, parsed.sub_total
    assert parsed.total_amount == 2138.40, parsed.total_amount
    assert parsed.descriptions == [
        "Fee for GOLS Transaction Certificates for 1st July 2020 to 31st Dec 2020"
    ], parsed.descriptions


check("parser: subtotal 1,980.00 not treated as year + cents total", _hexagon_subtotal_and_cents_total)


def _sub_total():
    assert invoice_parser.extract_sub_total(_SAMPLE_TEXT) == 1345.2
    assert invoice_parser.extract_sub_total("No labels here") is None


check("parser: extract_sub_total uses Sub Total label only", _sub_total)


def _total_amount():
    assert invoice_parser.extract_total_amount(_SAMPLE_TEXT) == 1345.2
    spoken = "Total : Euro One Thousand Three Hundred Forty Five and 20/100"
    assert invoice_parser.extract_total_amount(spoken) == 1345.2
    assert invoice_parser.extract_total_amount("VAT 0% only") is None


check("parser: extract_total_amount label ranking + words", _total_amount)


def _parse_invoice():
    parsed = invoice_parser.parse_invoice(_SAMPLE_TEXT)
    assert parsed.client == "Acme Trading Ltd"
    assert parsed.invoice_no == "INV-2026-001"
    assert parsed.invoice_date == "8-Jun-26"
    assert parsed.currency == "Euro"
    assert parsed.total_amount == 1345.2
    assert parsed.extraction_status == "processed"
    assert "Missing total amount" not in parsed.warnings
    assert "Sub total not found" not in parsed.warnings
    assert parsed.to_supabase_record()["reviewed"] is False


check("parser: parse_invoice full sample", _parse_invoice)


def _parse_hint_precedence():
    parsed = invoice_parser.parse_invoice(
        _SAMPLE_TEXT,
        hint={"total_amount": 999.5, "invoice_no": "HINT-1", "client": "Hint Ltd"},
    )
    assert parsed.total_amount == 999.5
    assert parsed.invoice_no == "HINT-1"
    assert parsed.client == "Hint Ltd"


check("parser: structured hint wins over rules", _parse_hint_precedence)


def _parse_zero_amounts():
    parsed = invoice_parser.parse_invoice(
        "Client: Zero Ltd\nInvoice No: Z-1\nInvoice Date: 8-Jun-26\n"
        "Credit note line    0.00\nTotal 0.00",
        hint={"sub_total": 0.0},
    )
    assert parsed.sub_total == 0.0, parsed.sub_total
    assert parsed.total_amount == 0.0
    assert "Sub total not found" not in parsed.warnings
    parsed = invoice_parser.parse_invoice("Some text without a total.")
    assert parsed.extraction_status == "needs_review"
    assert "Missing total amount" in parsed.warnings


check("parser: genuine 0.0 amounts kept, status needs_review", _parse_zero_amounts)


def _invoice_to_row():
    parsed = invoice_parser.parse_invoice(_SAMPLE_TEXT)
    row = invoice_parser.invoice_to_row(parsed)
    assert row["Client"] == "Acme Trading Ltd"
    assert "\n" in row["Description"]
    assert row["Total amount"] == 1345.2


check("parser: invoice_to_row joins descriptions", _invoice_to_row)


# ---------------------------------------------------------------------------
# 4. Validators
# ---------------------------------------------------------------------------


def _validate_row():
    good = {
        "Client": "M", "Invoice No": "I-1", "Invoice Date": "8-Jun-26",
        "Description": "d", "Sub total": 1.0, "Total amount": 2.0,
        "Currency": "Euro",
    }
    assert validate_row(good) == []
    missing_subtotal = {**good, "Sub total": None}
    assert validate_row(missing_subtotal) == ["Sub total is missing (optional)"]
    bad = {**good, "Client": "", "Total amount": "abc"}
    issues = validate_row(bad)
    assert "Missing client name" in issues
    assert "Total amount is missing or not numeric" in issues


check("validators: validate_row warnings only", _validate_row)


def _needs_review():
    good = {
        "Client": "M", "Invoice No": "I-1", "Invoice Date": "8-Jun-26",
        "Description": "d", "Total amount": 2.0, "Currency": "Euro",
    }
    assert validators.needs_review(good) is False
    assert validators.can_auto_save(good) is True
    assert validators.needs_review({**good, "Invoice No": ""}) is True
    assert validators.needs_review({**good, "Total amount": None}) is True
    assert validators.needs_review({**good, "Client": ""}) is True
    # Missing Sub total alone never blocks saving.
    assert validators.needs_review({**good, "Sub total": None}) is False


check("validators: needs_review / can_auto_save", _needs_review)


def _validate_rows():
    import pandas as pd

    frame = pd.DataFrame(
        [
            {"Client": "M", "Invoice No": "I-1", "Invoice Date": "8-Jun-26",
             "Description": "d", "Sub total": 1.0, "Total amount": 1.0,
             "Currency": "Euro"},
            {"Client": "", "Invoice No": "I-2", "Invoice Date": "8-Jun-26",
             "Description": "d", "Sub total": 1.0, "Total amount": None,
             "Currency": "Euro"},
        ]
    )
    results = validate_rows(frame)
    assert [r["row"] for r in results] == [2]
    assert "Missing client name" in results[0]["issues"]
    assert "Total amount is missing or not numeric" in results[0]["issues"]
    assert validate_rows([]) == []


check("validators: validate_rows 1-based row numbers", _validate_rows)


# ---------------------------------------------------------------------------
# 5. Supabase service (mocked client)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, state=None):
        self.state = state or {}
        self.filters = []
        self.select_args = None

    def select(self, *args, **kwargs):
        self.state["select"] = True
        self.select_args = args
        self.state["select_args"] = args
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def ilike(self, column, value):
        self.filters.append(("ilike", column, value))
        return self

    def order(self, column, desc=False):
        self.state["order"] = (column, desc)
        return self

    def limit(self, n):
        self.state["limit"] = n
        return self

    def range(self, start, end):
        self.state["range"] = (start, end)
        return self

    def maybe_single(self):
        self.state["maybe_single"] = True
        return self

    def upsert(self, payload, on_conflict=None):
        self.state["upsert"] = payload
        self.state["on_conflict"] = on_conflict
        return self

    def update(self, payload):
        self.state["update"] = payload
        return self

    def execute(self):
        return _FakeResponse(
            self.state.get("fake_data", []), self.state.get("fake_count")
        )


class _FakeTable:
    def __init__(self, state):
        self.state = state

    def select(self, *args, **kwargs):
        return _FakeQuery(self.state).select(*args, **kwargs)

    def upsert(self, payload, on_conflict=None):
        q = _FakeQuery(self.state)
        q.state["upsert"] = payload
        q.state["on_conflict"] = on_conflict
        return q


class _FakeClient:
    def __init__(self):
        self.state = {"fake_data": [], "fake_count": None}

    def table(self, name):
        assert name == "invoice_records", name
        return _FakeTable(self.state)


def _install_fake_client():
    client = _FakeClient()
    supabase_service._client = client  # noqa: SLF001
    return client


def _sb_upsert():
    client = _install_fake_client()
    n = supabase_service.upsert_invoice_record(
        {"client": "M", "invoice_no": "INV-1", "invoice_date": "8-Jun-26",
         "description": "d", "sub_total": 1140.0, "total_amount": 1345.20,
         "currency": "Euro", "source_file_name": "a.pdf",
         "raw_text": "raw", "extraction_status": "processed", "reviewed": False}
    )
    assert n == 1
    upsert = client.state["upsert"]
    assert client.state["on_conflict"] == "invoice_no"
    assert len(upsert) == 1
    assert upsert[0]["invoice_date"] == "2026-06-08"
    assert upsert[0]["reviewed"] is False
    assert upsert[0]["total_amount"] == 1345.2


check("supabase: upsert on invoice_no conflict", _sb_upsert)


def _sb_upsert_preserves_review():
    client = _install_fake_client()
    client.state["upsert"] = None
    n = supabase_service.upsert_invoice_record(
        {"client": "M", "invoice_no": "INV-1", "invoice_date": "8-Jun-26",
         "description": "d", "sub_total": 1140.0, "total_amount": 1345.20,
         "currency": "Euro", "source_file_name": "a.pdf",
         "raw_text": "raw", "extraction_status": "processed"}
    )
    assert n == 1
    payload = client.state["upsert"][0]
    assert "reviewed" not in payload, payload
    client.state["upsert"] = None
    n = supabase_service.upsert_invoice_record(
        {"client": "M", "invoice_no": "INV-2", "reviewed": True}
    )
    assert n == 1
    assert client.state["upsert"][0]["reviewed"] is True


check("supabase: upsert preserves reviewed unless explicitly set", _sb_upsert_preserves_review)


def _sb_upsert_skips_empty():
    client = _install_fake_client()
    n = supabase_service.upsert_invoice_records(
        [{"client": "X", "invoice_no": "  "}, {"client": "Y", "invoice_no": "B-2"}]
    )
    assert n == 1
    assert len(client.state["upsert"]) == 1
    assert client.state["upsert"][0]["invoice_no"] == "B-2"


check("supabase: upsert skips rows without invoice_no", _sb_upsert_skips_empty)


def _sb_get_records():
    client = _install_fake_client()
    client.state["fake_data"] = [
        {"id": 1, "client": "Metshu", "invoice_no": "INV-1",
         "invoice_date": "2026-06-08", "description": "d",
         "sub_total": 1140.0, "total_amount": 1345.2, "currency": "Euro",
         "extraction_status": "processed", "reviewed": False}
    ]
    records = supabase_service.get_invoice_records()
    assert len(records) == 1
    assert records[0]["invoice_no"] == "INV-1"
    records = supabase_service.get_invoice_records_by_status("needs_review")
    assert len(records) == 1


check("supabase: get_invoice_records + by status", _sb_get_records)


def _sb_summary_totals():
    client = _install_fake_client()
    client.state["fake_data"] = [
        {"client": "A", "invoice_no": "I-1", "sub_total": 1345.2,
         "total_amount": 1345.2, "currency": "Euro",
         "extraction_status": "processed", "reviewed": True,
         "source_file_name": "a.pdf"},
        {"client": "B", "invoice_no": "I-2", "sub_total": 53.1,
         "total_amount": 53.1, "currency": "USD",
         "extraction_status": "needs_review", "reviewed": False,
         "source_file_name": "a.pdf"},
        {"client": "C", "invoice_no": "I-3", "sub_total": None,
         "total_amount": 0, "currency": "Euro",
         "extraction_status": "processed", "reviewed": False,
         "source_file_name": "b.pdf"},
    ]
    summary = supabase_service.get_invoice_summary_totals()
    assert summary["count"] == 3
    assert summary["needs_review"] == 1, summary
    assert summary["reviewed"] == 1, summary
    assert summary["source_files"] == 2, summary
    by_currency = summary["by_currency"]
    assert by_currency == [
        {"currency": "Euro", "count": 2, "total_amount": 1345.2},
        {"currency": "USD", "count": 1, "total_amount": 53.1},
    ], by_currency


check("supabase: summary totals + per-currency aggregates", _sb_summary_totals)


def _sb_summary_projection():
    client = _install_fake_client()
    client.state["fake_data"] = [{"sub_total": 1.0, "total_amount": 2.0}]
    supabase_service.get_invoice_summary_totals()
    expected = (
        "sub_total, total_amount, currency, extraction_status, reviewed, "
        "source_file_name"
    )
    assert client.state["select_args"] == (expected,), client.state["select_args"]


check("supabase: summary totals only fetch aggregate columns", _sb_summary_projection)


def _sb_normalize():
    rec = _normalize_record({
        "client": " M ", "invoice_no": "X1", "invoice_date": "8-Jun-26",
        "description": " d ", "sub_total": "1,140.00", "total_amount": "53.1",
        "currency": " eur ", "extraction_status": "", "reviewed": "yes",
    })
    assert rec["client"] == "M"
    assert rec["invoice_date"] == "2026-06-08"
    assert rec["sub_total"] == 1140.0
    assert rec["total_amount"] == 53.1
    assert rec["extraction_status"] == "processed"
    assert rec["reviewed"] is True
    rec = _normalize_record({
        "client": "M", "invoice_no": "X2", "invoice_date": "8-Jun-26",
        "description": "d", "sub_total": 1.0, "total_amount": 2.0,
        "currency": "eur",
    })
    assert "reviewed" not in rec, rec
    # Empty/absent metadata is never sent: partial upserts (e.g. ticking the
    # Reviewed checkbox) must not wipe raw_text or the source PDF name.
    assert "raw_text" not in rec and "source_file_name" not in rec, rec
    rec = _normalize_record({
        "client": "M", "invoice_no": "X3", "invoice_date": "8-Jun-26",
        "description": "d", "sub_total": 1.0, "total_amount": 2.0,
        "currency": "eur", "raw_text": "text", "source_file_name": "a.pdf",
        "reviewed": False,
    })
    assert rec["raw_text"] == "text"
    assert rec["source_file_name"] == "a.pdf"
    assert rec["reviewed"] is False
    assert _to_iso_date("01/02/2026") is None
    assert _to_optional_float("") is None


check("supabase: record normalisation", _sb_normalize)


def _sb_friendly_error():
    err = _friendly_error(RuntimeError("PGRST205 table not in schema cache"))
    assert "sql/schema.sql" in str(err)
    assert "SQL Editor" in str(err)


check("supabase: missing-table hint on PGRST205", _sb_friendly_error)


# ---------------------------------------------------------------------------
# 6. Config resolution (SUPABASE_SECRET_KEY first, legacy fallback)
# ---------------------------------------------------------------------------


def _config_resolution():
    import dotenv

    real_load_dotenv = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: None  # ignore the real .env
    os.environ.pop("SUPABASE_SECRET_KEY", None)
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    try:
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_new"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "legacy"
        importlib.reload(config_module)
        assert config_module.SUPABASE_SERVICE_ROLE_KEY == "sb_secret_new"
        os.environ.pop("SUPABASE_SECRET_KEY")
        importlib.reload(config_module)
        assert config_module.SUPABASE_SERVICE_ROLE_KEY == "legacy"
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY")
        importlib.reload(config_module)
        assert config_module.SUPABASE_SERVICE_ROLE_KEY is None
    finally:
        dotenv.load_dotenv = real_load_dotenv
        importlib.reload(config_module)  # restore the .env-resolved values


check("config: SUPABASE_SECRET_KEY preferred over legacy", _config_resolution)


# ---------------------------------------------------------------------------
# 7. UI helpers
# ---------------------------------------------------------------------------


def _ui_record_row_roundtrip():
    rec = {
        "client": "Metshu", "invoice_no": "INV-1",
        "invoice_date": date(2026, 6, 8), "description": "TC Charges",
        "sub_total": 1140.0, "total_amount": 1345.2, "currency": "Euro",
        "reviewed": False,
    }
    row = ui_helpers.record_to_row(rec)
    assert row["Invoice Date"] == "8-Jun-26", row
    assert row["Sub total"] == 1140.0
    record = ui_helpers.row_to_record(row, source_file_name="a.pdf",
                                      raw_text="raw", reviewed=True)
    assert record["client"] == "Metshu"
    assert record["sub_total"] == 1140.0
    assert record["reviewed"] is True
    assert record["source_file_name"] == "a.pdf"
    record = ui_helpers.row_to_record(row)
    assert "reviewed" not in record, record


check("ui_helpers: record <-> row roundtrip", _ui_record_row_roundtrip)


def _ui_export_bytes():
    blob = ui_helpers.export_bytes_from_records(
        [{"client": "M", "invoice_no": "I-1", "invoice_date": "2026-06-08",
          "description": "d", "sub_total": 1.0, "total_amount": 2.0,
          "currency": "EUR"}]
    )
    assert isinstance(blob, bytes) and len(blob) > 1000


check("ui_helpers: export_bytes_from_records", _ui_export_bytes)


def _ui_cached_export_db():
    client = _install_fake_client()
    client.state["fake_data"] = [
        {"client": "M", "invoice_no": "I-1", "invoice_date": "2026-06-08",
         "description": "d", "sub_total": 1.0, "total_amount": 2.0,
         "currency": "EUR", "raw_text": "raw", "reviewed": False},
        {"client": "M", "invoice_no": "I-2", "invoice_date": "2026-06-09",
         "description": "d2", "sub_total": 3.0, "total_amount": 4.0,
         "currency": "EUR", "raw_text": "raw2", "reviewed": True},
    ]
    ui_helpers.cached_export_bytes_from_db.clear()
    blob = ui_helpers.cached_export_bytes_from_db()
    assert isinstance(blob, bytes) and len(blob) > 1000
    client.state["fake_data"] = [
        {"client": "CHANGED", "invoice_no": "I-9", "invoice_date": "2026-06-10",
         "description": "different", "sub_total": 9.0, "total_amount": 9.0,
         "currency": "EUR", "raw_text": "x", "reviewed": False},
    ]
    blob2 = ui_helpers.cached_export_bytes_from_db()
    assert blob == blob2  # cached within ttl: no second fetch


check("ui_helpers: cached_export_bytes_from_db cached", _ui_cached_export_db)


# ---------------------------------------------------------------------------
# 8. Processing: auto chunking + auto upsert
# ---------------------------------------------------------------------------


def _batch_sizes():
    files = [{"name": f"f{i}.pdf", "data": b"x" * 100_000} for i in range(10)]
    assert processing.calculate_batch_size(files) == 10  # <=20 -> all
    small = [{"name": f"f{i}.pdf", "data": b"x" * 100_000} for i in range(30)]
    assert processing.calculate_batch_size(small) == 50  # avg 0.1MB -> 50
    med = [{"name": f"f{i}.pdf", "data": b"x" * 2_000_000} for i in range(30)]
    assert processing.calculate_batch_size(med) == 25  # avg 2MB -> 25
    large = [{"name": f"f{i}.pdf", "data": b"x" * 6_000_000} for i in range(30)]
    assert processing.calculate_batch_size(large) == 10  # avg 6MB -> 10
    assert processing.calculate_batch_size([]) == 0
    assert processing.calculate_batch_size(None) == 0


check("processing: calculate_batch_size thresholds", _batch_sizes)


def _process_files_auto_upsert():
    text = _SAMPLE_TEXT
    client = _install_fake_client()

    def fake_extract(file_bytes, azure_enabled=False, ocr_enabled=True):
        return (text, "pdfplumber", None)

    processing.pdf_extractor.extract_text_from_pdf = fake_extract
    old_enabled = supabase_service.is_enabled
    supabase_service.is_enabled = lambda: True
    try:
        result = processing.process_files(
            [{"name": "m1.pdf", "data": b"x" * 100}], {"ocr_enabled": True}
        )
    finally:
        supabase_service.is_enabled = old_enabled
    assert result["summary"]["processed"] == 1
    assert result["summary"]["success"] == 1
    assert result["summary"]["needs_review"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["inserted_updated"] == 1, client.state["upsert"]
    assert result["summary"]["files"] == ["m1.pdf"]
    assert len(result["preview"]) == 1
    assert client.state["on_conflict"] == "invoice_no"
    assert "reviewed" not in client.state["upsert"][0]


check("processing: process_files auto-upserts valid rows", _process_files_auto_upsert)


def _process_dedup():
    client = _install_fake_client()
    different = _SAMPLE_TEXT.replace("INV-2026-001", "INV-2026-002")

    def fake_extract(file_bytes, azure_enabled=False, ocr_enabled=True):
        name = file_bytes.decode()
        return (_SAMPLE_TEXT if name.startswith("a") else different, "pdfplumber", None)

    processing.pdf_extractor.extract_text_from_pdf = fake_extract
    recorded = []

    def recording_upsert(record):
        recorded.append(record)
        return 1

    old_upsert = supabase_service.upsert_invoice_record
    old_enabled = supabase_service.is_enabled
    supabase_service.upsert_invoice_record = recording_upsert
    supabase_service.is_enabled = lambda: True
    try:
        result = processing.process_files(
            [{"name": "a.pdf", "data": b"a" * 100},
             {"name": "b.pdf", "data": b"b" * 100}],
            {"ocr_enabled": True},
        )
    finally:
        supabase_service.upsert_invoice_record = old_upsert
        supabase_service.is_enabled = old_enabled
    assert result["summary"]["inserted_updated"] == 2
    assert len(result["preview"]) == 2
    invoice_nos = [r["invoice_no"] for r in recorded]
    assert "INV-2026-001" in invoice_nos and "INV-2026-002" in invoice_nos
    assert all("reviewed" not in r for r in recorded)


check("processing: preview dedupe keeps distinct invoices", _process_dedup)


def _process_empty_invoice_no():
    client = _install_fake_client()
    text = (
        "Client: NoNumber Ltd\nInvoice Date: 8-Jun-26\n"
        "Services rendered    500.00\nTotal 500.00\n"
    )

    def fake_extract(file_bytes, azure_enabled=False, ocr_enabled=True):
        return (text, "pdfplumber", None)

    processing.pdf_extractor.extract_text_from_pdf = fake_extract
    old_enabled = supabase_service.is_enabled
    supabase_service.is_enabled = lambda: True
    try:
        result = processing.process_files(
            [{"name": "n1.pdf", "data": b"x" * 100},
             {"name": "n2.pdf", "data": b"x" * 100}],
            {"ocr_enabled": True},
        )
    finally:
        supabase_service.is_enabled = old_enabled
    assert result["summary"]["needs_review"] == 2
    assert result["summary"]["inserted_updated"] == 0
    # Both files stay visible in the preview - "" must not collide as a key.
    assert len(result["preview"]) == 2
    assert all(row["Invoice No"] == "" for row in result["preview"])


check("processing: empty invoice numbers never overwrite each other", _process_empty_invoice_no)


def _process_method_none():
    def fake_extract(file_bytes, azure_enabled=False, ocr_enabled=True):
        return ("", "none", None)

    processing.pdf_extractor.extract_text_from_pdf = fake_extract
    result = processing.process_files(
        [{"name": "bad.pdf", "data": b"x" * 100}], {"ocr_enabled": True}
    )
    assert result["summary"]["processed"] == 1
    assert result["summary"]["failed"] == 1
    assert result["summary"]["inserted_updated"] == 0
    assert len(result["preview"]) == 0  # nothing usable -> no junk rows
    assert result["statuses"]["bad.pdf"]["status"] == "Failed"


check("processing: method=none is a failure, never a junk row", _process_method_none)


def _process_files_chunking():
    client = _install_fake_client()

    def fake_extract(file_bytes, azure_enabled=False, ocr_enabled=True):
        raise RuntimeError("cannot read")

    processing.pdf_extractor.extract_text_from_pdf = fake_extract
    files = [{"name": f"f{i}.pdf", "data": b"x" * 6_000_000} for i in range(25)]
    result = processing.process_files(
        files, {"ocr_enabled": False, "azure_enabled": False}, batch_size=10
    )
    assert result["summary"]["processed"] == 25
    assert result["summary"]["failed"] == 25
    assert result["summary"]["inserted_updated"] == 0
    assert result["summary"]["batch_size"] == 10
    assert len(result["preview"]) == 0  # nothing usable -> no junk rows


check("processing: internal chunking of large uploads", _process_files_chunking)


# ---------------------------------------------------------------------------
# 9. Schema + structure
# ---------------------------------------------------------------------------


def _schema():
    path = os.path.join(ROOT, "sql", "schema.sql")
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()
    assert "invoice_records" in sql
    assert "unique (invoice_no)" in sql or "unique(invoice_no)" in sql
    assert "sub_total" in sql and "total_amount" in sql
    assert "add column if not exists" in sql.lower()
    assert "extraction_status" in sql and "reviewed" in sql


check("schema.sql: invoice_records + migration", _schema)


def _streamlit_version():
    import streamlit

    assert streamlit.__version__ >= "1.36", streamlit.__version__


check("streamlit: supports st.navigation (>= 1.36)", _streamlit_version)


def _pages_exist():
    for module in ("dashboard", "upload_process", "saved_records", "settings"):
        path = os.path.join(ROOT, "pages", f"{module}.py")
        assert os.path.exists(path), path
    assert not os.path.exists(os.path.join(ROOT, "pages", "review_edit.py"))
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
        app_source = fh.read()
    assert "st.navigation" in app_source
    # File-based st.Page entries with unique filenames: URL pathnames are
    # inferred from the filenames, so callable pages (all named "render")
    # would collide and raise "Multiple Pages specified with URL pathname".
    for module in ("dashboard", "upload_process", "saved_records", "settings"):
        assert f'st.Page("pages/{module}.py"' in app_source, module


check("structure: 4 pages + st.navigation entry", _pages_exist)


if __name__ == "__main__":
    raise SystemExit(run_all())
