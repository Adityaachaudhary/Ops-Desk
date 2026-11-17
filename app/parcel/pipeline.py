"""Case 1 per-image pipeline: validate -> OCR overlay -> parse fields -> scene check -> honest status.

Status taxonomy (see parcel/README.md):
  SUCCESS            AWB + weight + dimensions read, nothing suspicious
  PARTIAL_EXTRACTION AWB read, weight and/or dimensions missing (fields that were read are kept)
  LOW_CONFIDENCE     fields read but OCR confidence / digit ambiguity / plausibility says "check me"
  LABEL_UNREADABLE   no AWB could be read from the frame
  NO_PARCEL / MULTIPLE_PARCELS / PARTIAL_PARCEL   scene problems (any fields that were read are still shown)
  LABEL_BLOCKED      parcel present, but no shipping-label barcode region found on it
  INVALID_IMAGE      file is not a readable image
"""
from pathlib import Path

from PIL import Image, ImageFile

from . import fields

ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = 200_000_000

SUCCESS, PARTIAL_EX, LOW_CONF = "SUCCESS", "PARTIAL_EXTRACTION", "LOW_CONFIDENCE"
UNREADABLE, NO_PARCEL, MULTI, PARTIAL_PARCEL = "LABEL_UNREADABLE", "NO_PARCEL", "MULTIPLE_PARCELS", "PARTIAL_PARCEL"
BLOCKED, INVALID = "LABEL_BLOCKED", "INVALID_IMAGE"
ALL_STATUSES = [SUCCESS, PARTIAL_EX, LOW_CONF, UNREADABLE, NO_PARCEL, MULTI, PARTIAL_PARCEL, BLOCKED, INVALID]
MIN_OCR_CONF = 50.0


def _score(text: str) -> int:
    o = fields.parse_overlay(text)
    return (o.awb is not None) + (o.weight_kg is not None) + o.dims_complete


def _blank(name: str, status: str, note: str) -> dict:
    return {"filename": name, "awb": "", "weight_kg": None, "length_cm": None, "width_cm": None, "height_cm": None,
            "dimensions": "", "status": status, "needs_review": 1, "confidence": 0.0, "parcels_detected": None, "notes": note}


def process_image(path: Path) -> dict:
    from . import ocr, scene  # lazy: Tesseract is needed for Case 1 only
    name = Path(path).name
    try:
        with Image.open(path) as im:
            im.load()
            img = im.convert("RGB")
    except Exception as e:
        return _blank(name, INVALID, f"Not a readable image ({type(e).__name__}); file skipped.")
    if min(img.size) < 80:
        return _blank(name, INVALID, f"Image too small ({img.size[0]}x{img.size[1]}).")

    text, conf, box = ocr.read_overlay(img, _score)
    o = fields.parse_overlay(text)
    sc = scene.analyse(img, box)
    notes = list(o.notes)
    issues = fields.plausibility_issues(o)
    parcels = sc["parcels"]
    barcode_awbs = []
    for value in sc.get("barcode_values", []):
        if value.isascii() and value.isalnum() and 8 <= len(value) <= 24:
            parsed = fields.parse_overlay(f"Barcode: {value}")
            if parsed.awb:
                barcode_awbs.append(parsed.awb)
    barcode_awbs = list(dict.fromkeys(barcode_awbs))
    if o.awb is None and len(barcode_awbs) == 1:
        o.awb = barcode_awbs[0]
        conf = 100.0
        notes.append("AWB decoded from the physical shipping-label barcode")
    elif o.awb and barcode_awbs and o.awb not in barcode_awbs:
        o.risky.append("physical-label barcode disagrees with scanner-overlay AWB")

    label_conf = None
    if o.awb is None or o.weight_kg is None or not o.dims_complete:
        label_text, label_conf = ocr.read_label(img, _score, box)
        label = fields.parse_overlay(label_text)
        recovered = False
        for attr, label_name in (("awb", "AWB"), ("weight_kg", "weight"), ("length_cm", "length"),
                                 ("width_cm", "width"), ("height_cm", "height")):
            current, candidate = getattr(o, attr), getattr(label, attr)
            if current is None and candidate is not None:
                setattr(o, attr, candidate)
                notes.append(f"{label_name} recovered from physical-label OCR")
                recovered = True
            elif current is not None and candidate is not None and current != candidate:
                o.risky.append(f"physical-label OCR disagrees with scanner-overlay {label_name}")
        notes.extend(label.notes)
        o.risky.extend(label.risky)
        if recovered:
            conf = min(conf, label_conf) if conf > 0 else label_conf
        if label.awb and o.awb == label.awb:
            conf = min(conf, label_conf) if conf > 0 else label_conf

    status = None
    if parcels == 0:
        status = NO_PARCEL
        notes.append("no parcel-like object found in frame")
        if o.weight_kg is not None and o.dims_complete:
            notes.append(f"scanner still reported {o.weight_kg:g} kg and dimensions - the frame may just be too dark; verify manually")
    elif parcels > 1:
        status = MULTI
        notes.append(f"{parcels} parcel-like objects in frame; overlay data cannot be tied to one parcel")
    elif any(sc["cut"]):
        status = PARTIAL_PARCEL
        notes.append("parcel touches the frame edge (partly visible)")
    if status is None:
        if o.awb is None:
            status = UNREADABLE
            if sc["barcodes"]:
                notes.append("barcode region detected, but its value could not be decoded; label may be covered or unreadable")
            else:
                notes.append("no AWB/barcode text could be read from the scanner overlay or physical label")
        elif o.risky or issues or conf < MIN_OCR_CONF:
            status = LOW_CONF
            notes += o.risky + issues
            if conf < MIN_OCR_CONF:
                notes.append(f"OCR confidence {conf:.0f}% is low")
        elif o.weight_kg is not None and o.dims_complete:
            status = SUCCESS
        else:
            status = PARTIAL_EX
            missing = [k for k, v in (("weight", o.weight_kg), ("dimensions", o.dims_complete or None)) if v is None]
            notes.append("missing: " + ", ".join(missing))
    else:
        if o.risky or issues:
            notes += o.risky + issues
    confidence = 0.0 if not o.awb else round(min(100.0, conf) * (0.6 if (o.risky or issues) else 1.0), 1)
    return {"filename": name, "awb": o.awb or "", "weight_kg": o.weight_kg, "length_cm": o.length_cm, "width_cm": o.width_cm,
            "height_cm": o.height_cm, "dimensions": o.dims_text(), "status": status, "needs_review": 0 if status == SUCCESS else 1,
            "confidence": confidence, "parcels_detected": parcels, "notes": "; ".join(dict.fromkeys(notes))}
