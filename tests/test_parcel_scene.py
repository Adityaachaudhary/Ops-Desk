"""Scene verdict + sensor fusion (pure logic, no OCR needed) and a synthetic-image smoke test."""
import numpy as np
from PIL import Image

from app.parcel import scene


def _sc(verdict, conf=0.9, parcels=1, reason="", **kw):
    return {"verdict": verdict, "scene_conf": conf, "scene_reason": reason, "parcels": parcels, **kw}


def test_no_parcel_never_asserted_when_scanner_measured_weight_and_dims():
    st, conf, why = scene.fuse(_sc("NO_PARCEL", 0.85, parcels=0), True, True)
    assert st == "UNSURE" and "scanner" in why


def test_no_parcel_asserted_when_picture_empty_and_scanner_silent():
    st, _, _ = scene.fuse(_sc("NO_PARCEL", 0.85, parcels=0), False, False)
    assert st == "NO_PARCEL"


def test_weak_multi_and_partial_become_unsure():
    assert scene.fuse(_sc("MULTIPLE_PARCELS", 0.5, parcels=2), True, True)[0] == "UNSURE"
    assert scene.fuse(_sc("PARTIAL_PARCEL", 0.4), True, True)[0] == "UNSURE"
    assert scene.fuse(_sc("MULTIPLE_PARCELS", 0.85, parcels=2), True, True)[0] == "MULTIPLE_PARCELS"


def test_legacy_scene_dict_without_verdict_still_works():
    assert scene.fuse({"parcels": 2, "cut": [False, False]}, True, True)[0] == "MULTIPLE_PARCELS"
    assert scene.fuse({"parcels": 1, "cut": [False]}, True, True)[0] == "OK"


def _frame(boxes, size=(900, 700)):
    img = np.full((size[1], size[0], 3), 18, np.uint8)
    rng = np.random.default_rng(0)
    for (x0, y0, x1, y1) in boxes:
        patch = 120 + rng.integers(-25, 25, (y1 - y0, x1 - x0, 1))
        img[y0:y1, x0:x1] = patch.astype(np.uint8)
    return Image.fromarray(img)


def test_one_parcel_is_one_parcel():
    sc = scene.analyse(_frame([(250, 200, 650, 520)]))
    assert sc["parcels"] == 1 and sc["verdict"] == "OK"


def test_two_separate_parcels_are_multiple():
    sc = scene.analyse(_frame([(60, 120, 360, 420), (520, 200, 840, 560)]))
    assert sc["verdict"] == "MULTIPLE_PARCELS"


def test_empty_belt_is_no_parcel():
    sc = scene.analyse(_frame([]))
    assert sc["parcels"] == 0 and sc["verdict"] == "NO_PARCEL"


def test_strip_frame_without_visible_parcel_is_no_parcel_even_with_scanner_data():
    sc = _sc("NO_PARCEL", 0.72, parcels=0, reason="truncated line-scan frame with no parcel clearly visible")
    assert scene.fuse(sc, True, True)[0] == "NO_PARCEL"


def test_thin_dark_strip_analysed_as_no_parcel():
    img = Image.fromarray(np.full((120, 900, 3), 12, np.uint8))
    sc = scene.analyse(img)
    assert sc["verdict"] in ("NO_PARCEL",)