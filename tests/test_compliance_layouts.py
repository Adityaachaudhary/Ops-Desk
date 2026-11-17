from decimal import Decimal

import pytest

from app.compliance import parser
from app.compliance.parser import EcrParse
from app.compliance.verifier import AMT_MM, MATCHED, REVIEW, verify
from app.core.jobs import UserInputError


def _stub_page(monkeypatch, text):
    monkeypatch.setattr(parser, "_pages_text", lambda _path: iter([(text, False)]))


def test_pf_parser_joins_a_wrapped_member_row(monkeypatch):
    _stub_page(
        monkeypatch,
        "Total Members 1\nTotal EPF Contribution 1200\n"
        "Member Details:\n"
        "Sl. No UAN Name as per ECR UAN Repository Wages Wages Wages Wages EE EPS ER Days\n"
        "1 123456789012 ANIL\n"
        "KUMAR ANIL KUMAR 10000 10000 10000 10000 1200 1000 500 0",
    )

    parsed = parser.parse_pf_ecr("wrapped.pdf")

    assert len(parsed.rows) == 1
    assert parsed.rows[0]["name"] == "ANIL KUMAR"
    assert parsed.rows[0]["amount"] == Decimal("1200")


def test_esic_parser_joins_a_wrapped_member_row(monkeypatch):
    _stub_page(
        monkeypatch,
        "Total IP Contribution\n75.00\n"
        "SNo. Is Disable IP Number IP Name No. Of Days Total Wages IP Contribution Reason\n"
        "1 - 123456789012 ANIL\n"
        "KUMAR 30 10000.00 75.00 -",
    )

    parsed = parser.parse_esic_ecr("wrapped.pdf")

    assert len(parsed.rows) == 1
    assert parsed.rows[0]["name"] == "ANIL KUMAR"
    assert parsed.rows[0]["amount"] == Decimal("75.00")


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (
            "PF",
            "Total Members 1\nMember Details:\nSl. No UAN EE ER EPS Days\n"
            "1 123456789012 ANIL KUMAR 10000 1200 1000 500 0",
        ),
        (
            "ESIC",
            "SNo. IP Number IP Name IP Contribution Total Wages No. Of Days\n"
            "1 - 123456789012 ANIL KUMAR 75.00 10000.00 30",
        ),
    ],
)
def test_parser_rejects_reordered_amount_columns(monkeypatch, kind, text):
    _stub_page(monkeypatch, text)

    with pytest.raises(UserInputError, match="column order"):
        if kind == "PF":
            parser.parse_pf_ecr("reordered.pdf")
        else:
            parser.parse_esic_ecr("reordered.pdf")


def test_amount_comparison_is_exact_by_default():
    ws = [{
        "uan": "123456789012", "ip": "", "name": "ANIL KUMAR",
        "pf_amt": Decimal("100.00"), "pf_raw": "100.00",
        "esic_amt": None, "esic_raw": "", "agency": "", "state": "",
    }]
    pf = EcrParse("PF", rows=[{
        "id": "123456789012", "name": "ANIL KUMAR", "amount": Decimal("100.01"),
    }])

    results, _ = verify(ws, pf, EcrParse("ESIC"))

    assert results[0]["pf_status"] == AMT_MM
    assert results[0]["pf_diff"] == "0.01"


def test_explicit_approved_tolerance_can_be_applied():
    ws = [{
        "uan": "123456789012", "ip": "", "name": "ANIL KUMAR",
        "pf_amt": Decimal("100.00"), "pf_raw": "100.00",
        "esic_amt": None, "esic_raw": "", "agency": "", "state": "",
    }]
    pf = EcrParse("PF", rows=[{
        "id": "123456789012", "name": "ANIL KUMAR", "amount": Decimal("100.50"),
    }])

    results, _ = verify(ws, pf, EcrParse("ESIC"), tolerance=Decimal("0.50"))

    assert results[0]["pf_status"] == MATCHED


def test_ocr_uncertain_pf_row_requires_manual_review():
    ws = [{
        "uan": "123456789012", "ip": "", "name": "ANIL KUMAR",
        "pf_amt": Decimal("100.00"), "pf_raw": "100.00",
        "esic_amt": None, "esic_raw": "", "agency": "", "state": "",
    }]
    pf = EcrParse("PF", rows=[{
        "id": "123456789012", "name": "ANIL KUMAR", "amount": Decimal("100.00"),
        "ocr_ambiguous": True,
    }])

    results, _ = verify(ws, pf, EcrParse("ESIC"))

    assert results[0]["pf_status"] == REVIEW
