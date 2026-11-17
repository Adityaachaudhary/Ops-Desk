"""Scene analysis with plain OpenCV (no model weights): how many parcels, are they cut off,
is a shipping label (barcode) visible. Heuristic by design - see parcel/README.md for limits."""
import cv2
import numpy as np
from PIL import Image

_BARCODE = None


def _barcode_detector():
    global _BARCODE
    if _BARCODE is None:
        try:
            _BARCODE = cv2.barcode.BarcodeDetector()
        except Exception:
            _BARCODE = False
    return _BARCODE


def analyse(img: Image.Image, text_box=None, work_w: int = 900) -> dict:
    g = np.array(img.convert("L"))
    H0, W0 = g.shape
    s = work_w / W0
    g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    H, W = g.shape
    if text_box:  # blank out the overlay text so it is not mistaken for a parcel
        x0, y0, x1, y1 = [int(v * s) for v in text_box]
        g[max(0, y0 - 6): y1 + 6, max(0, x0 - 6): x1 + 6] = 0
    blur = cv2.GaussianBlur(g, (7, 7), 0)
    bg = np.percentile(blur, 35)
    bright = (blur > max(bg + 28, 45)).astype(np.uint8) * 255
    # texture: parcels (plastic, tape, print) have much higher local gradient than the belt
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1)
    mag = cv2.GaussianBlur(cv2.magnitude(gx, gy), (9, 9), 0)
    textured = (mag > 22).astype(np.uint8) * 255
    mask = cv2.bitwise_and(bright, textured)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    min_area = 0.012 * W * H
    comps = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < min_area:
            continue
        # belt edges / line-scan artefacts show up as thin full-height bands, not parcels
        if min(w, h) < 0.07 * min(W, H) or max(w / h, h / w) > 5.5:
            continue
        ys, xs = np.where(lab[y:y + h, x:x + w] == i)
        span_l = (xs == 0).sum() / H if x <= 2 else 0
        span_r = (xs == w - 1).sum() / H if x + w >= W - 2 else 0
        span_b = (ys == h - 1).sum() / W if y + h >= H - 2 else 0
        span_t = (ys == 0).sum() / W if (y <= 2 and not text_box) else 0
        cut = max(span_l, span_r, span_b, span_t) >= 0.30
        comps.append({"area": int(a), "bbox": (int(x), int(y), int(w), int(h)), "cut": bool(cut), "frac": round(float(a) / (W * H), 3)})
    comps.sort(key=lambda c: -c["area"])
    if comps:
        big = comps[0]["area"]
        comps = [c for c in comps if c["area"] >= 0.60 * big]
    barcodes, barcode_values = 0, []
    det = _barcode_detector()
    if det and comps:
        try:
            ok, decoded, _, pts = det.detectAndDecodeWithType(g)
            barcodes = len(pts) if pts is not None else 0
            barcode_values = [value.strip() for value in decoded if value and value.strip()]
            if not ok and not barcodes:
                found, pts = det.detect(g)
                barcodes = len(pts) if found and pts is not None else 0
        except Exception:
            try:
                found, pts = det.detect(g)
                barcodes = len(pts) if found and pts is not None else 0
            except Exception:
                barcodes = 0
            barcode_values = []
    return {"parcels": len(comps), "cut": [c["cut"] for c in comps], "fracs": [c["frac"] for c in comps],
            "barcodes": barcodes, "barcode_values": barcode_values}
