from pathlib import Path

import pytest
from PIL import Image

from app.parcel import ocr, pipeline, scene


def _scene(parcels=1, barcode_values=None):
    return {"parcels": parcels, "cut": [False] if parcels else [], "barcodes": len(barcode_values or []),
            "barcode_values": barcode_values or []}


def test_physical_label_ocr_fills_missing_fields(tmp_path, monkeypatch):
    image_path = tmp_path / "parcel.png"
    Image.new("RGB", (240, 180), "white").save(image_path)
    monkeypatch.setattr(ocr, "read_overlay", lambda *_: ("", 0.0, None))
    monkeypatch.setattr(ocr, "read_label", lambda *_: (
        "Tracking ID: 123456789012\nWeight: 1.2 kg\nDimensions: 10x20x30 cm", 90.0))
    monkeypatch.setattr(scene, "analyse", lambda *_: _scene())

    result = pipeline.process_image(image_path)

    assert result["awb"] == "123456789012"
    assert result["weight_kg"] == 1.2
    assert result["dimensions"] == "10x20x30 cm"
    assert result["status"] == "SUCCESS"
    assert "physical-label OCR" in result["notes"]


def test_decoded_label_barcode_is_used_when_overlay_is_missing(tmp_path, monkeypatch):
    image_path = tmp_path / "parcel.png"
    Image.new("RGB", (240, 180), "white").save(image_path)
    monkeypatch.setattr(ocr, "read_overlay", lambda *_: ("", 0.0, None))
    monkeypatch.setattr(ocr, "read_label", lambda *_: ("", 0.0))
    monkeypatch.setattr(scene, "analyse", lambda *_: _scene(barcode_values=["123456789012"]))

    result = pipeline.process_image(image_path)

    assert result["awb"] == "123456789012"
    assert result["status"] == "PARTIAL_EXTRACTION"
    assert "decoded from the physical shipping-label barcode" in result["notes"]


def test_wider_overlay_crop_recovers_visible_weight():
    pytest.importorskip("pytesseract")
    if ocr.find_tesseract() is None:
        pytest.skip("Tesseract is not installed")
    image_path = (Path(__file__).resolve().parents[1] / "data" / "sample" / "case1" /
                  "No Shipment packet captured_png.rf.1VvRtdnwIQAHs1Q83pHV.png")

    result = pipeline.process_image(image_path)

    assert result["weight_kg"] == 1.21
