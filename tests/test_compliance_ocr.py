import shutil
from pathlib import Path
from decimal import Decimal

import pytest

from app.core.ocr_compat import pytesseract_module

try:
    with pytesseract_module():
        pass
except ImportError:
    pytest.skip("pytesseract is not installed", allow_module_level=True)
pytest.importorskip("pypdfium2")
from fpdf import FPDF
from PIL import Image

from app.compliance.parser import EcrParse, parse_esic_ecr, parse_pf_ecr
from app.compliance.verifier import REVIEW, verify


def _has_tesseract():
    if shutil.which("tesseract"):
        return True
    return any(Path(p).is_file() for p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ))


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract is not installed")
def test_scanned_pf_pdf_uses_ocr_fallback(tmp_path):
    source = tmp_path / "source.pdf"
    pdf = FPDF()
    pdf.set_font("Courier", size=10)
    pdf.add_page()
    pdf.multi_cell(0, 6, "Total Members 1\nTotal EPF Contribution 1200\n"
                         "1 123456789012 ANIL KUMAR ANIL KUMAR 10000 10000 10000 10000 1200 1000 500 0")
    pdf.output(str(source))

    import pypdfium2
    bitmap = pypdfium2.PdfDocument(str(source))[0].render(scale=4).to_pil()
    image_path = tmp_path / "page.png"
    bitmap.save(image_path)
    scanned = tmp_path / "scanned.pdf"
    image_pdf = FPDF()
    image_pdf.add_page()
    image_pdf.image(str(image_path), x=0, y=0, w=210, h=297)
    image_pdf.output(str(scanned))

    parsed = parse_pf_ecr(scanned)
    assert len(parsed.rows) == 1
    assert parsed.rows[0]["id"] == "123456789012"
    assert parsed.rows[0]["amount"] == 1200
    assert parsed.rows[0]["ocr_ambiguous"]
    assert any("verify this member manually" in warning for warning in parsed.warnings)
    ws_row = {
        "uan": "123456789012", "ip": "", "name": "ANIL KUMAR", "pf_amt": Decimal("1200"),
        "pf_raw": "1200", "esic_amt": None, "esic_raw": "", "agency": "", "state": "",
    }
    results, _ = verify([ws_row], parsed, EcrParse("ESIC"))
    assert results[0]["pf_status"] == REVIEW
    assert "OCR was uncertain" in results[0]["pf_reason"]


@pytest.mark.skipif(not _has_tesseract(), reason="Tesseract is not installed")
def test_scanned_esic_pdf_uses_ocr_fallback(tmp_path):
    source = tmp_path / "source.pdf"
    pdf = FPDF()
    pdf.set_font("Courier", size=10)
    pdf.add_page()
    pdf.multi_cell(0, 6, "Total IP Contribution\n75.00\n"
                         "1 ABC123 123456789012 ANIL KUMAR 30 10000.00 75.00")
    pdf.output(str(source))

    import pypdfium2
    bitmap = pypdfium2.PdfDocument(str(source))[0].render(scale=4).to_pil()
    image_path = tmp_path / "page.png"
    bitmap.save(image_path)
    scanned = tmp_path / "scanned.pdf"
    image_pdf = FPDF()
    image_pdf.add_page()
    image_pdf.image(str(image_path), x=0, y=0, w=210, h=297)
    image_pdf.output(str(scanned))

    parsed = parse_esic_ecr(scanned)
    assert len(parsed.rows) == 1
    assert parsed.rows[0]["id"] == "123456789012"
    assert parsed.rows[0]["name"] == "ANIL KUMAR"
    assert parsed.rows[0]["amount"] == Decimal("75.00")
