from app.parcel.fields import parse_overlay


def test_yellow_template():
    o = parse_overlay("AWB No: 19106721737143\nLength: 30.6 cm\nWidth: 43 cm\nHeight: 5.4 cm\nR.Vol.: 5930 cm (cubic)\nWeight: 0.78 kg")
    assert (o.awb, o.weight_kg, o.dims_text()) == ("19106721737143", 0.78, "30.6x43x5.4 cm")


def test_concatenated_mm_template_and_grams():
    o = parse_overlay("Barcode:19108610817924\nDimension:L0213XH0148XW0178\nWeight:01190")
    assert o.dims_text() == "21.3x17.8x14.8 cm" and o.weight_kg == 1.19


def test_awb_junk_suffix_and_decimal_dims():
    o = parse_overlay("AWB: 152880I80157016,-26482_1747717072,Feedlane2\nDimension(LxBxH): 15.5x11.1x7.7 cm\nWeight: 0.42 Kgs")
    assert o.awb == "152880I80157016" and o.risky  # letter among digits -> flagged risky
    assert o.dims_text() == "15.5x11.1x7.7 cm"


def test_zero_dimensions_are_not_reported():
    o = parse_overlay("Barcode: PROC000019107\nWeight: 0.67 kg\nL * W * H: 0000 * 0000 * 0000")
    assert o.length_cm is None and "zero" in " ".join(o.notes)


def test_ocr_zero_glyphs_safe_but_noted():
    o = parse_overlay("Barcode: 31049516469061\nDimension:L@318XH@044XW@314\nWeight :01250")
    assert o.dims_text() == "31.8x31.4x4.4 cm"


def test_nothing_found():
    o = parse_overlay("MID: 34982\nDate Time: 2025-07-20")
    assert o.awb is None and o.weight_kg is None and not o.dims_complete


def test_awb_noise_and_noread():
    assert parse_overlay("AWB_No: 90150050340/").awb == "90150050340"
    o = parse_overlay("AWB No: 19106721625482TTOO")
    assert o.awb == "19106721625482" and any("dropped" in r for r in o.risky)  # kept but flagged for review
    o = parse_overlay("Barcode: 2DNOREAD\nWeight: 70.0 kg")
    assert o.awb is None and any("NOREAD" in n for n in o.notes)
