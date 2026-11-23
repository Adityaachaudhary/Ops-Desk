"""Tesseract access for Case 1 ONLY. Imported lazily so the other modules never need it."""
import os
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from ..core.ocr_compat import pytesseract_module

# Where the Windows installers (winget / UB-Mannheim) put tesseract.exe when PATH was not updated
WINDOWS_DEFAULTS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


class OcrUnavailable(RuntimeError):
    pass


def find_tesseract() -> str | None:
    """1) TESSERACT_CMD env var  2) PATH  3) usual Windows install folders."""
    env = os.getenv("TESSERACT_CMD")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    for p in WINDOWS_DEFAULTS:
        if os.path.isfile(p):
            return p
    return None


_RAPID = None


def _rapid():
    """RapidOCR (PaddleOCR models on ONNX runtime): free, CPU-only, models ship inside the pip package."""
    global _RAPID
    if _RAPID is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID = RapidOCR()
        except Exception:
            _RAPID = False
    return _RAPID


def ensure_ocr():
    exe = find_tesseract()
    if exe is None and _rapid():
        return                      # RapidOCR alone is enough for the scanner overlay; Tesseract is the optional fallback
    if exe is None:
        raise OcrUnavailable(
            "Tesseract OCR binary not found. Install it (Windows: winget install UB-Mannheim.TesseractOCR; "
            "Ubuntu: apt install tesseract-ocr; macOS: brew install tesseract), then restart the app. "
            "If it is installed in a custom folder, set the TESSERACT_CMD environment variable to the full path of tesseract.exe. "
            "The Compliance and P&L modules work without it."
        )
    try:
        subprocess.run([exe, "--version"], capture_output=True, timeout=10, check=True)
        with pytesseract_module() as pytesseract:
            pytesseract.pytesseract.tesseract_cmd = exe
    except (ImportError, subprocess.SubprocessError, OSError) as e:
        raise OcrUnavailable("pytesseract could not use the installed Tesseract executable "
                             "(pip install pytesseract and verify Tesseract is working).") from e


def _prep(crop: Image.Image, target_w: int, mode: str) -> np.ndarray:
    import cv2
    rgb = np.array(crop.convert("RGB"))
    if mode == "adaptive":
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    else:  # bright overlay text (yellow/green/white): max channel keeps coloured text bright
        g = rgb.max(axis=2)
    scale = target_w / g.shape[1]
    g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    if mode == "adaptive":
        bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, -25)
    else:
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw = 255 - bw  # Tesseract prefers dark text on light
    return cv2.copyMakeBorder(bw, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def _colour_isolate(arr: np.ndarray) -> np.ndarray | None:
    """Coloured overlay text (yellow / green / red) over a bright or busy picture: keep only strongly coloured pixels
    and return black-on-white. None if the crop has no coloured text."""
    mx, mn = arr.max(axis=2).astype(np.int16), arr.min(axis=2).astype(np.int16)
    mask = ((mx - mn) > 0.45 * np.maximum(mx, 1)) & (mx > 140)
    share = float(mask.mean())
    if not (0.0008 < share < 0.20):
        return None
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((2, 2), np.uint8))
    out = np.where(mask > 0, 0, 255).astype(np.uint8)
    return np.stack([out] * 3, axis=2)


def _rapid_pass(img: Image.Image, region=(0.0, 0.0, 0.62, 0.62), max_w=1800, min_w=0, colour=False):
    """One RapidOCR pass over a region (fractions of the image). Returns (text, conf 0-100, box_px_or_None)."""
    eng = _rapid()
    W, H = img.size
    x0, y0, x1, y1 = int(W * region[0]), int(H * region[1]), int(W * region[2]), int(H * region[3])
    crop = img.crop((x0, y0, x1, y1)).convert("RGB")
    cw = crop.size[0]
    target = max_w if cw > max_w else (min_w if 0 < cw < min_w else cw)       # shrink big crops, enlarge tiny images
    if target != cw:
        crop = crop.resize((target, max(1, round(crop.size[1] * target / cw))), Image.Resampling.LANCZOS)
    f = cw / crop.size[0]
    arr = np.asarray(crop)
    if colour:
        arr = _colour_isolate(arr)
        if arr is None:
            return "", 0.0, None
    out, _ = eng(arr)
    if not out:
        return "", 0.0, None
    items = []
    for b, txt, c in out:
        ys = [p[1] for p in b]
        xs = [p[0] for p in b]
        items.append({"y": sum(ys) / 4, "h": max(ys) - min(ys), "x0": min(xs), "x1": max(xs), "y1": max(ys), "t": txt, "c": float(c)})
    items.sort(key=lambda i: (i["y"], i["x0"]))
    tol = max(10.0, 0.6 * float(np.median([i["h"] for i in items])))
    lines, cur, last = [], [], None
    for it in items:
        if last is not None and abs(it["y"] - last) > tol and cur:
            lines.append(" ".join(i["t"] for i in sorted(cur, key=lambda i: i["x0"])))
            cur = []
        cur.append(it)
        last = it["y"]
    if cur:
        lines.append(" ".join(i["t"] for i in sorted(cur, key=lambda i: i["x0"])))
    left = [i for i in items if i["x0"] < 0.40 * crop.size[0]]          # overlay text is left-aligned; ignore the label
    box = (x0, y0, int(min(max(i["x1"] for i in left) * f + x0 + 20, W)), int(min(max(i["y1"] for i in left) * f + y0 + 20, H))) if left else None
    return "\n".join(lines), float(np.mean([i["c"] for i in items])) * 100.0, box


def _read_overlay_rapid(img: Image.Image, score_fn=None):
    """Read the scanner overlay with RapidOCR using several passes until all 3 fields (AWB, weight, dimensions) parse:
      A top-left block (as before)  B same block, colour-isolated text  C native-resolution crop around the block
      D whole image, enlarged if small (white-panel layout prints its data at the bottom)
    Text from all passes tried is merged, so a field found by any pass counts. Returns (text, conf, box) or None."""
    if not _rapid():
        return None
    score = score_fn or (lambda t: 0)
    results = []

    def run(**kw):
        try:
            r = _rapid_pass(img, **kw)
        except Exception:
            return None
        if r[0]:
            results.append(r)
        return r

    def best_so_far():
        merged = "\n".join(r[0] for r in results)
        return merged, score(merged)

    run()
    if score(best_so_far()[0]) < 3:
        run(colour=True)
    if score(best_so_far()[0]) < 3 and results:
        box = next((r[2] for r in results if r[2]), None)
        if box and (img.size[0] > 2600):                                   # re-read the block at native resolution
            W, H = img.size
            run(region=(0, 0, min(1.0, box[2] * 1.25 / W), min(1.0, box[3] * 1.4 / H)), max_w=2400)
    if score(best_so_far()[0]) < 3:
        W, H = img.size
        run(region=(0, 0, 1, 1), max_w=2400, min_w=1800 if W < 1400 else 0)
    if not results:
        return "", 0.0, None
    text, _ = best_so_far()
    conf = float(np.mean([r[1] for r in results]))
    box = next((r[2] for r in results if r[2]), None)
    return text, conf, box


ATTEMPTS = [("otsu", 1400), ("otsu", 2600), ("adaptive", 2000), ("otsu", 3800)]


def read_overlay(img: Image.Image, score_fn):
    """RapidOCR first (more accurate on this overlay text); Tesseract only if RapidOCR did not find all 3 fields.
    Returns (text, mean_conf 0-100, text_box_px)."""
    best = None
    r = _read_overlay_rapid(img, score_fn)
    if r is not None:
        best = (r[0], r[1], r[2], score_fn(r[0]))
        if best[3] >= 3:
            return best[0], best[1], best[2]
    if find_tesseract() is not None:
        t = _read_overlay_tesseract(img, score_fn)
        ts = score_fn(t[0])
        if best is None or ts > best[3] or (ts == best[3] and t[1] > best[1]):
            return t
    return (best[0], best[1], best[2]) if best else ("", 0.0, None)


def _read_overlay_tesseract(img: Image.Image, score_fn):
    """OCR the top-left overlay block with Tesseract. Tries several preprocessing variants and keeps the best parse.
    score_fn(text) -> int (number of fields found, 3 = complete). Returns (text, mean_conf, text_box_px)."""
    with pytesseract_module():
        pass
    import pytesseract
    from pytesseract import Output

    W, H = img.size
    regions = [(0, 0, int(W * 0.62), int(H * 0.34))]
    best = ("", 0.0, None, -1)
    for ri, box_ in enumerate(regions + [(0, 0, W, int(H * 0.55))]):
        if ri == 1 and best[3] > 0:
            break
        crop = img.crop(box_)
        for mode, target_w in ATTEMPTS:
            arr = _prep(crop, target_w, mode)
            d = pytesseract.image_to_data(arr, config="--psm 6", output_type=Output.DICT)
            lines, confs = {}, []
            for i, t in enumerate(d["text"]):
                t = t.strip()
                if not t:
                    continue
                key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                lines.setdefault(key, []).append((d["left"][i], t))
                try:
                    c = float(d["conf"][i])
                except ValueError:
                    c = -1
                if c >= 0:
                    confs.append(c)
            text = "\n".join(" ".join(w for _, w in sorted(v)) for k, v in sorted(lines.items()))
            sc = score_fn(text)
            conf = float(np.mean(confs)) if confs else 0.0
            if sc > best[3] or (sc == best[3] and conf > best[1]):
                ys = [d["top"][i] + d["height"][i] for i, t in enumerate(d["text"]) if t.strip()]
                f = crop.size[0] / arr.shape[1]
                box = (0, 0, crop.size[0], int(min(max(ys, default=0) * f + 20, crop.size[1]))) if ys else None
                best = (text, conf, box, sc)
            if sc >= 3:
                break
        if best[3] >= 3:
            break
    return best[0], best[1], best[2]


def read_label(img: Image.Image, score_fn, overlay_box=None):
    """Best-effort OCR on the parcel/label area, with the scanner overlay masked out."""
    if find_tesseract() is None:
        return "", 0.0
    with pytesseract_module():
        pass
    import pytesseract
    from pytesseract import Output

    crop = img.convert("RGB").copy()
    if overlay_box:
        ImageDraw.Draw(crop).rectangle(overlay_box, fill=(0, 0, 0))
    gray = ImageOps.autocontrast(crop.convert("L"))
    target_w = 2400
    if gray.width < target_w:
        scale = target_w / gray.width
        gray = gray.resize((target_w, max(1, round(gray.height * scale))), Image.Resampling.LANCZOS)
    arr = np.asarray(gray)
    best_text, best_conf, best_score = "", 0.0, -1
    for psm in (11, 6):
        data = pytesseract.image_to_data(arr, config=f"--psm {psm}", output_type=Output.DICT)
        lines, confs = {}, []
        for i, word in enumerate(data["text"]):
            word = word.strip()
            if not word:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((data["left"][i], word))
            try:
                conf = float(data["conf"][i])
            except ValueError:
                conf = -1
            if conf >= 0:
                confs.append(conf)
        text = "\n".join(" ".join(w for _, w in sorted(words)) for _, words in sorted(lines.items()))
        confidence = float(np.mean(confs)) if confs else 0.0
        score = score_fn(text)
        if score > best_score or (score == best_score and confidence > best_conf):
            best_text, best_conf, best_score = text, confidence, score
        if score >= 3:
            break
    return best_text, best_conf