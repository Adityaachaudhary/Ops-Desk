"""Scene analysis with plain OpenCV (no model weights): how many parcels, are they cut off.

Returns a verdict *with a confidence* instead of a hard answer, and `fuse()` combines it with what the scanner
itself measured (weight / dimensions). Heuristic by design - when it is not sure it says UNSURE so a person looks.

v2 changes (why the old detector was wrong):
  * the old code blanked the whole OCR text box (often ~20% of the frame) and erased real parcels -> NO_PARCEL;
    now thin text strokes are removed with a morphological opening and only blobs mostly *inside* the text box are dropped.
  * the old code needed a "textured" mask, which smooth grey wrap fails -> parcel missed; now contrast vs the belt only.
  * the old code kept every fragment >=60% of the biggest blob, so one parcel split into wrap + label became
    MULTIPLE_PARCELS; now fragments are merged with a large closing and a second object must be clearly separate.
"""
import cv2
import numpy as np
from PIL import Image

_BARCODE = None

THR_K = 0.18                   # object threshold = belt level + this share of the frame's contrast
BELT_BUSY = 6.0                # mean inner gradient below this = featureless lit belt, not a parcel
COLOUR_CHROMA = 8.0            # mean max-min channel spread above this = colour camera frame, not the grey belt scanner
BRIGHT_MEDIAN = 95.0           # median brightness above this = white panel / washed-out frame
STRIP_ASPECT = 0.40            # height/width below this = truncated line-scan strip
PARTIAL_MIN_SPAN = 0.40        # parcel must run off the frame edge along this share of that edge to be called PARTIAL
PARTIAL_MIN_BUSY = 9.0         # ...and be clearly textured (a real parcel, not lit belt)
SCENE_MIN_CONF = 0.60          # below this a scene verdict is not trusted -> UNSURE -> manual review
OK, NO_PARCEL, MULTI, PARTIAL, UNSURE = "OK", "NO_PARCEL", "MULTIPLE_PARCELS", "PARTIAL_PARCEL", "UNSURE"


def _barcode_detector():
    global _BARCODE
    if _BARCODE is None:
        try:
            _BARCODE = cv2.barcode.BarcodeDetector()
        except Exception:
            _BARCODE = False
    return _BARCODE


def _edge_sharpness(mag: np.ndarray, mask: np.ndarray, W: int, H: int) -> float:
    """Share of the object's boundary (ignoring the frame border) that is a hard edge.
    A parcel on a belt has a crisp outline; a lit patch of belt / light fall-off has a soft one."""
    m8 = mask.astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    band = cv2.dilate(m8, k) - cv2.erode(m8, k)
    band[:8, :] = 0; band[-8:, :] = 0; band[:, :8] = 0; band[:, -8:] = 0
    vals = mag[band > 0]
    if vals.size < 30:
        return 0.0
    return round(float((vals > 6).mean()), 3)


def _blobs(g: np.ndarray, text_box, scale: float):
    """Return (components, stats dict). Components are merged parcel-like objects."""
    H, W = g.shape
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    dark_half = blur[blur <= np.percentile(blur, 60)]
    bg = float(np.median(dark_half)) if dark_half.size else float(np.median(blur))     # belt level = the dark majority
    spread = float(np.percentile(blur, 99.5) - bg)
    thr = max(bg + max(14.0, THR_K * spread), 30)                    # scales with frame contrast: belt glow stays below it
    m = (blur > thr).astype(np.uint8) * 255
    k_open = max(9, W // 45)                                     # removes text strokes / scan noise
    k_close = max(15, W // 14)                                   # glues wrap + tape + label into one object
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy) / 4.0
    agx, agy = np.abs(gx) / 4.0, np.abs(gy) / 4.0                                   # ~ grey levels per pixel at the edge
    tb = None
    if text_box:
        tb = [int(v * scale) for v in text_box]
    comps, weak = [], 0
    for i in range(1, n):
        x, y, w, h, a = [int(v) for v in st[i]]
        frac = a / (W * H)
        if frac < 0.006:
            continue
        if tb:  # a blob that lives inside the overlay text block is text, not a parcel
            ix = max(0, min(x + w, tb[2]) - max(x, tb[0]))
            iy = max(0, min(y + h, tb[3]) - max(y, tb[1]))
            if ix * iy >= 0.6 * w * h:
                continue
        thin = min(w, h) < 0.08 * min(W, H) or max(w / h, h / w) > 6        # belt edges / line-scan bands
        if frac < 0.02 or thin:
            weak += 1
            continue
        comp_mask = (lab[y:y + h, x:x + w] == i)
        cm = (lab == i)
        sharp = _edge_sharpness(mag, cm, W, H)
        inner = cv2.erode(cm.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))) > 0
        streak = round(float(agx[inner].mean() / (agy[inner].mean() + 0.5)), 2) if inner.sum() > 50 else 1.0
        busy = round(float(mag[inner].mean()), 2) if inner.sum() > 50 else 0.0
        ys, xs = np.where(comp_mask)
        span_l = (xs == 0).sum() / H if x <= 2 else 0.0
        span_r = (xs == w - 1).sum() / H if x + w >= W - 2 else 0.0
        span_b = (ys == h - 1).sum() / W if y + h >= H - 2 else 0.0
        span_t = (ys == 0).sum() / W if y <= 2 else 0.0
        edges_touched = (x <= 2) + (y <= 2) + (x + w >= W - 2) + (y + h >= H - 2)
        if busy < BELT_BUSY and (edges_touched >= 2 or max(span_l, span_r, span_b, span_t) >= 0.30):
            weak += 1            # featureless lit belt / light fall-off that runs off the frame: not a parcel
            continue
        comps.append({"frac": round(frac, 4), "bbox": (x, y, w, h), "cut_span": round(max(span_l, span_r, span_b, span_t), 3),
                      "fill": round(a / float(w * h), 3), "sharp": sharp, "streak": streak, "busy": busy})
    comps.sort(key=lambda c: -c["frac"])
    return comps, {"bg": bg, "spread": spread, "weak": weak}


def _edge_rescue(g: np.ndarray, text_box, scale: float):
    """Second pass when the brightness pass found nothing: dark plastic bags and smooth grey wrap are not much
    brighter than the belt but they have creases and a crisp outline. Find objects by edge density instead."""
    H, W = g.shape
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy) / 4.0
    e = (mag > 7).astype(np.uint8) * 255
    if text_box:
        x0, y0, x1, y1 = [int(v * scale) for v in text_box]
        e[max(0, y0 - 4): y1 + 4, max(0, x0 - 4): x1 + 4] = 0
    e[:6, :] = 0; e[-6:, :] = 0; e[:, :6] = 0; e[:, -6:] = 0
    k = max(15, W // 16)
    e = cv2.morphologyEx(e, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    cnts, _ = cv2.findContours(e, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(e)
    cv2.drawContours(filled, cnts, -1, 255, -1)
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(9, W // 30),) * 2))
    n, lab, st, _ = cv2.connectedComponentsWithStats(filled)
    out = []
    for i in range(1, n):
        x, y, w, h, a = [int(v) for v in st[i]]
        frac = a / (W * H)
        if frac < 0.03 or frac > 0.75 or min(w, h) < 0.10 * min(W, H) or max(w / h, h / w) > 6:
            continue
        if (x <= 2) + (y <= 2) + (x + w >= W - 2) + (y + h >= H - 2) >= 3:
            continue
        out.append({"frac": round(frac, 4), "bbox": (x, y, w, h), "cut_span": 0.0, "fill": round(a / float(w * h), 3),
                    "sharp": 0.0, "streak": 1.0, "busy": 0.0, "rescued": True})
    out.sort(key=lambda c: -c["frac"])
    return out


def analyse(img: Image.Image, text_box=None, work_w: int = 640) -> dict:
    rgb_small = np.array(img.convert("RGB").resize((160, max(1, round(160 * img.height / img.width)))))
    chroma = float((rgb_small.max(axis=2).astype(int) - rgb_small.min(axis=2)).mean())
    g = np.array(img.convert("L"))
    H0, W0 = g.shape
    layout_other = chroma > COLOUR_CHROMA or float(np.median(g)) > BRIGHT_MEDIAN   # CCTV colour / white panel frames
    strip = (H0 / W0) < STRIP_ASPECT                                                  # truncated line-scan strips
    s = work_w / W0
    g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    H, W = g.shape
    comps, info = _blobs(g, text_box, s)
    rescued = False
    if not comps:
        comps = _edge_rescue(g, text_box, s)
        rescued = bool(comps)

    # --- verdict + confidence from the picture alone ---------------------------------------------------------
    reason, verdict, conf = "", OK, 0.0
    if layout_other and comps:
        comps = comps[:1]
    if comps:  # keep objects that are a meaningful share of the biggest one
        comps = [c for c in comps if c["frac"] >= 0.20 * comps[0]["frac"]]
    big = comps[0]["frac"] if comps else 0.0
    if not comps:
        if info["spread"] < 22 and info["weak"] == 0:
            verdict, conf, reason = NO_PARCEL, 0.85, "frame is empty belt (no object brighter than the belt)"
        else:
            verdict, conf, reason = UNSURE, 0.35, "something is in the frame but it is too small/faint to call a parcel"
    elif layout_other:
        verdict, conf, reason = UNSURE, 0.45, "colour / white-panel camera layout is not analysable by the belt model"
    elif big > 0.80:
        verdict, conf, reason = UNSURE, 0.45, "object fills almost the whole frame (camera/colour layout not analysable by the belt model)"
    elif rescued:  # found by edges only: good enough to say "a parcel is here", not to count or call it cut
        if len(comps) == 1 and comps[0]["frac"] >= 0.10:
            verdict, conf, reason = OK, 0.62, "parcel found by edge pass (low contrast / dark wrap)"
        else:
            verdict, conf, reason = UNSURE, 0.40, "only a small or fragmentary object found - parcel count/visibility not reliable"
    elif len(comps) >= 2:
        ratio = comps[1]["frac"] / big
        if strip:
            verdict, conf, reason = UNSURE, 0.45, "truncated line-scan strip with several objects - count not reliable"
        elif ratio >= 0.35 and comps[1]["frac"] >= 0.03:
            verdict, conf, reason = MULTI, min(0.9, 0.62 + 0.4 * ratio), f"{len(comps)} separate objects (2nd is {ratio:.0%} of the largest)"
        else:
            verdict, conf, reason = UNSURE, 0.45, "a second smaller object is present - could be a 2nd parcel or debris"
    else:
        c = comps[0]
        if c["cut_span"] >= PARTIAL_MIN_SPAN and c["busy"] >= PARTIAL_MIN_BUSY and not strip:
            verdict, conf, reason = PARTIAL, min(0.9, 0.55 + c["cut_span"]), "parcel runs off the frame edge"
        elif c["cut_span"] >= 0.12:
            verdict, conf, reason = UNSURE, 0.45, "parcel touches the frame edge - may be cut off"
        elif info["weak"] > 0 and big < 0.04:
            verdict, conf, reason = UNSURE, 0.5, "single small object plus other faint objects"
        else:
            conf = min(0.95, 0.65 + 4 * big)
            reason = "one complete parcel-sized object"

    if strip and verdict == UNSURE and ("too small/faint" in reason or "fragmentary" in reason or "faint objects" in reason):
        verdict, conf, reason = NO_PARCEL, 0.72, "truncated line-scan frame with no parcel clearly visible"

    # --- barcode (only used for AWB fallback / label evidence) -----------------------------------------------
    barcodes, barcode_values = 0, []
    det = _barcode_detector()
    if det and comps:
        try:
            gg = np.array(img.convert("L"))
            if gg.shape[1] > 1600:
                f = 1600 / gg.shape[1]
                gg = cv2.resize(gg, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            ok, decoded, _, pts = det.detectAndDecodeWithType(gg)
            barcodes = len(pts) if pts is not None else 0
            barcode_values = [v.strip() for v in decoded if v and v.strip()]
            if not ok and not barcodes:
                found, pts = det.detect(gg)
                barcodes = len(pts) if found and pts is not None else 0
        except Exception:
            barcodes, barcode_values = 0, []
    return {"parcels": len(comps), "cut": [c["cut_span"] >= 0.30 for c in comps], "fracs": [c["frac"] for c in comps],
            "barcodes": barcodes, "barcode_values": barcode_values,
            "verdict": verdict, "scene_conf": round(conf, 2), "scene_reason": reason, "weak_objects": info["weak"]}


def fuse(sc: dict, has_weight: bool, has_dims: bool) -> tuple[str, float, str]:
    """Combine the picture verdict with what the scanner measured. Returns (scene_status, scene_conf 0-100, reason).

    scene_status is one of OK / NO_PARCEL / MULTIPLE_PARCELS / PARTIAL_PARCEL / UNSURE.
    Rules (the scanner's weight + dimensions mean something physically sat on the scale):
      * never assert NO_PARCEL when the scanner reported a weight AND dimensions -> UNSURE (picture/sensor disagree)
      * picture says OK/uninformative but the scanner confirms a parcel -> OK (lower confidence if picture was unsure)
      * MULTI / PARTIAL are only asserted when the picture is confident (>= SCENE_MIN_CONF), otherwise UNSURE
    Older callers/tests that give no 'verdict' fall back to the parcel/cut counts."""
    verdict = sc.get("verdict")
    conf = float(sc.get("scene_conf", 0.9))
    reason = sc.get("scene_reason", "")
    if verdict is None:
        n = sc.get("parcels", 1)
        verdict = NO_PARCEL if n == 0 else MULTI if n > 1 else PARTIAL if any(sc.get("cut", [])) else OK
        conf = 0.9
    sensor = has_weight and has_dims
    if verdict == NO_PARCEL and sensor and "truncated line-scan" not in reason:
        return UNSURE, 40.0, "picture shows no parcel but the scanner reported weight and dimensions - verify"
    if verdict == UNSURE and sensor and ("fills almost the whole frame" in reason or "not analysable" in reason):
        return OK, 62.0, "scanner reported weight and dimensions; frame layout not analysable, parcel count not verified"
    if verdict == UNSURE and sensor and sc.get("parcels", 0) == 0:
        return UNSURE, round(conf * 100), reason + "; scanner reported weight and dimensions"
    if verdict in (MULTI, PARTIAL, NO_PARCEL) and conf < SCENE_MIN_CONF:
        return UNSURE, round(conf * 100), reason
    return verdict, round(conf * 100), reason