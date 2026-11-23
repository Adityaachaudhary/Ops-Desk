"""Overlay formats that used to leave AWB / weight / dimensions unextracted (found by reading real failing frames)."""
from app.parcel import fields


def _awb(t):
    o = fields.Overlay()
    return fields.parse_awb(t, o), o


def test_zero_for_o_in_barcode_key():
    assert _awb("Barc0de:41306503_19109110324251")[0] == "19109110324251"


def test_zero_for_o_in_awb_no_key():
    assert _awb("AWB N0: 19106721734671")[0] == "19106721734671"


def test_barcode_printed_three_times_is_collapsed():
    awb, o = _awb("Barcode:310495170100913104951701009131049517010091")
    assert awb == "31049517010091" and any("3x" in n for n in o.notes)


def test_barcode_printed_twice_is_collapsed():
    assert _awb("Barcode:1910672173703619106721737036")[0] == "19106721737036"


def test_proc_barcode_with_o_read_as_zero_and_1d_key():
    awb, _ = _awb("Barcode1D 473362_473362-s203019;PR0C0001129074\nBarcode2D NoRead")
    assert awb == "PROC0001129074"


def test_weight_value_on_same_row_as_next_label():
    o = fields.parse_overlay("AWB No: 31049517050875\nWeight: Time: 6/19/2025 1:59:11 AM 2450.000 gm FRAGILE")
    assert o.weight_kg == 2.45


def test_white_panel_template():
    o = fields.parse_overlay("MID: 211224\nL:40.4cm A.W: 1560 g\nW: 31.7 cm V.W: 18441.79 g\nH:14.4cm\nAWB:19106721735894")
    assert (o.awb, o.weight_kg, o.dims_text()) == ("19106721735894", 1.56, "40.4x31.7x14.4 cm")