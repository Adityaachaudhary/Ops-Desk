"""Tesseract access for Case 1 ONLY. Imported lazily so the other modules never need it."""
import os
import shutil

import numpy as np
from PIL import Image

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


def ensure_ocr():
    try:
        import pytesseract
    except ImportError as e:
        raise OcrUnavailable("pytesseract is not installed (pip install pytesseract).") from e
    exe = find_tesseract()
    if exe is None:
        raise OcrUnavailable(
            "Tesseract OCR binary not found. Install it (Windows: winget install UB-Mannheim.TesseractOCR; "
            "Ubuntu: apt install tesseract-ocr; macOS: brew install tesseract), then restart the app. "
            "If it is installed in a custom folder, set the TESSERACT_CMD environment variable to the full path of tesseract.exe. "
            "The Compliance and P&L modules work without it."
        )
    pytesseract.pytesseract.tesseract_cmd = exe


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


ATTEMPTS = [("otsu", 1400), ("otsu", 2600), ("adaptive", 2000), ("otsu", 3800)]


def read_overlay(img: Image.Image, score_fn):
    """OCR the top-left overlay block. Tries several preprocessing variants and keeps the best parse.
    score_fn(text) -> int (number of fields found, 3 = complete). Returns (text, mean_conf, text_box_px)."""
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