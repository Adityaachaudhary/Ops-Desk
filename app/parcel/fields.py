"""Pure-Python parsing of the machine overlay text printed on each scanner frame.

No OCR / OpenCV import here, so the logic can be unit-tested without Tesseract.
Several overlay templates exist in the sample data (see tests/test_parcel_fields.py).
OCR digit confusions are handled conservatively: '@/O/Q' -> 0 is treated as a safe read
(the monospace template prints a dotted zero) while I/l/S/B -> digit marks the record 'risky'.
"""
import re
from dataclasses import dataclass, field

SAFE = str.maketrans({"@": "0", "O": "0", "o": "0", "Q": "0", "D": "0"})
RISKY = str.maketrans({"I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})
DIG = r"[0-9OoQ@DIl|SB]"
NUM = rf"({DIG}+(?:\.{DIG}+)?)"
SEP = r"\s*[xX×*/]\s*"
KEY_L, KEY_W, KEY_H = r"l[eo]n\w{0,4}", r"w[il]d\w{0,3}", r"h[eo]i\w{0,4}!?"


@dataclass
class Overlay:
    awb: str | None = None
    weight_kg: float | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    volume_cm3: float | None = None
    notes: list[str] = field(default_factory=list)
    risky: list[str] = field(default_factory=list)  # reasons the read should not be trusted blindly

    @property
    def dims_complete(self) -> bool:
        return None not in (self.length_cm, self.width_cm, self.height_cm)

    def dims_text(self) -> str:
        if not self.dims_complete:
            return ""
        return f"{self.length_cm:g}x{self.width_cm:g}x{self.height_cm:g} cm"


class _Ctx:
    def __init__(self, o: Overlay):
        self.o = o

    def num(self, s: str) -> str:
        """Normalise OCR digit confusables inside a numeric token."""
        if re.fullmatch(r"[0-9.]+", s):
            return s
        t = s.translate(SAFE)
        if re.fullmatch(r"[0-9.]+", t):
            self.o.notes.append("OCR zero-like glyphs read as 0")
            return t
        t2 = t.translate(RISKY)
        self.o.risky.append(f"digit glyph ambiguity in '{s}'")
        return t2 if re.fullmatch(r"[0-9.]+", t2) else "x"


NOREAD = re.compile(r"(?i)NO[\s_]?READ|^(NULL|UNKNOWN|ERROR|N/?A)$")


def _clean_awb(raw: str, o: Overlay) -> str | None:
    raw = raw.split(",")[0]
    parts = [p for p in re.split(r"[_|]", raw) if p.strip()]
    if len(parts) > 1:  # "41306503_19109110324251" -> prefer the long numeric part
        longest = max(parts, key=lambda p: len(re.sub(r"\D", "", p)))
        if len(re.sub(r"\D", "", longest)) >= 10:
            raw = longest
    raw = re.sub(r"\s+", "", raw)
    if NOREAD.search(raw.strip("-_:.;|/")):
        o.notes.append(f"scanner printed '{raw}' instead of a barcode value")
        return None
    m = re.match(r"[A-Za-z0-9]+", raw.strip("-_:.;|/"))  # cut at the first stray symbol ("90150050340/" -> "90150050340")
    if not m:
        return None
    awb = m.group(0).upper()
    if not (8 <= len(awb) <= 24):
        return None
    m2 = re.fullmatch(r"(\d{10,16})([A-Z0-9]{1,8})", awb)
    if m2 and not m2.group(2).isdigit():  # digits followed by letters: OCR noise glued to the number
        o.risky.append(f"trailing characters '{m2.group(2)}' dropped from AWB (OCR noise)")
        awb = m2.group(1)
    if awb.startswith("PROC") and not awb[4:].isdigit():  # PROC-type barcodes are PROC + digits only
        o.risky.append(f"barcode '{awb}' has non-digit characters after PROC (OCR confusion)")    
    digits = sum(c.isdigit() for c in awb)
    letters = [c for c in awb if c.isalpha()]
    if digits >= 10 and 0 < len(letters) <= 2:
        o.risky.append(f"AWB has letter(s) {''.join(letters)} among digits (possible OCR confusion)")
    return awb


def parse_awb(text: str, o: Overlay) -> str | None:
    pat = re.compile(
        r"(?im)^\W*(?:AWB[\s_]*(?:No|Number|Num)?[\s_]*\.?|Bar\s*code|Tracking(?:\s*(?:No|Number))?|Waybill(?:\s*No)?)\s*[:：=;\-]?\s*(.+)$"
    )
    for m in pat.finditer(text):
        awb = _clean_awb(m.group(1), o)
        if awb:
            return awb
    return None


def parse_weight(text: str, o: Overlay) -> float | None:
    c = _Ctx(o)
    m = re.search(
        r"(?i)w[ea]i?[gq]h?t\s*(?:\(\s*(kgs?|g|gm)\s*\))?\s*[:：=;,]?\s*" + NUM + r"\s*(kgs?|kq|k9|gm|grams?|g)?\b", text)
    if not m:
        return None
    raw = c.num(m.group(2))
    if raw == "x":
        return None
    val = float(raw)
    unit = (m.group(1) or m.group(3) or "").lower()
    if unit.startswith("k"):
        kg = val
    elif unit in ("g", "gm", "gram", "grams"):
        kg = val / 1000
    elif "." in raw:
        kg = val
    elif val >= 100:
        kg = val / 1000
        o.notes.append("weight unit missing; integer >=100 read as grams")
        if len(raw) != 5:
            o.risky.append(f"weight digit count unexpected ('{raw}')")
    else:
        kg = val
        o.notes.append("weight unit missing; read as kg")
    if kg <= 0:
        o.notes.append("weight reported as zero")
        return None
    return round(kg, 3)


def _scale(vals: list[str], unit: str | None, o: Overlay) -> list[float]:
    unit = (unit or "").lower()
    padded = all(len(v) >= 4 and "." not in v for v in vals)
    nums = [float(v) for v in vals]
    if unit == "mm" or (not unit and padded):
        if not unit:
            o.notes.append("dimension unit missing; zero-padded integers read as mm")
        return [round(n / 10, 1) for n in nums]
    return [round(n, 1) for n in nums]


def _nonzero(vals: list[float], o: Overlay):
    if any(v <= 0 for v in vals):
        o.notes.append("scanner reported zero/invalid dimensions")
        return None
    return vals


def parse_dimensions(text: str, o: Overlay):
    c = _Ctx(o)
    parts = {}
    for key, rx in (("l", KEY_L), ("w", KEY_W), ("h", KEY_H)):
        m = re.search(rf"(?i)\b{rx}\s*(?:\(\s*(cm|mm)\s*\))?\s*[:：=;,]?\s*{NUM}\s*(c?m|mm)?", text)
        if m:
            parts[key] = (c.num(m.group(2)), m.group(1) or m.group(3))
    if len(parts) == 3 and all(v[0] != "x" for v in parts.values()):
        unit = next((u for _, u in parts.values() if u), "cm")
        unit = "cm" if unit and unit.lower() == "m" else unit
        return _nonzero(_scale([parts[k][0] for k in "lwh"], unit, o), o)
    m = re.search(r"(?i)L\s*([\w@]{3,5})\s*X\s*H\s*([\w@]{3,5})\s*X\s*[WN]\s*([\w@]{3,5})", text)
    if m:
        raw = [c.num(g) for g in m.groups()]
        if "x" not in raw:
            L, H, W = _scale(raw, "mm", o)
            return _nonzero([L, W, H], o)
    m = re.search(
        r"(?i)(?:[do]im\w{0,8}s?(?:\s*\([^)]*\))?|L\s*[*xX]\s*[WB]\s*[*xX]\s*H)\s*[:：=;,]?\s*"
        + NUM + SEP + NUM + SEP + NUM + r"\s*(cm|mm)?", text)
    if m:
        raw = [c.num(m.group(i)) for i in (1, 2, 3)]
        if "x" not in raw:
            return _nonzero(_scale(raw, m.group(4), o), o)
    return None


def parse_overlay(text: str) -> Overlay:
    o = Overlay()
    o.awb = parse_awb(text, o)
    o.weight_kg = parse_weight(text, o)
    dims = parse_dimensions(text, o)
    if dims:
        o.length_cm, o.width_cm, o.height_cm = dims
    m = re.search(r"(?i)(?:r\.?\s*vol|real\s*volume|volume)\w*\s*[:：=]?\s*(\d+(?:\.\d+)?)", text)
    if m:
        o.volume_cm3 = float(m.group(1))
    return o


def plausibility_issues(o: Overlay) -> list[str]:
    issues = []
    if o.weight_kg is not None and not (0.005 <= o.weight_kg <= 100):
        issues.append(f"weight {o.weight_kg} kg outside plausible range")
    if o.dims_complete:
        for n, v in zip("LWH", (o.length_cm, o.width_cm, o.height_cm)):
            if not (0.5 <= v <= 300):
                issues.append(f"{n}={v} cm outside plausible range")
        if o.volume_cm3 and o.volume_cm3 > 1000:
            calc = o.length_cm * o.width_cm * o.height_cm
            if abs(calc - o.volume_cm3) / o.volume_cm3 > 0.35 and abs(calc - o.volume_cm3) / calc > 0.35:
                issues.append(f"L*W*H ({calc:.0f}) disagrees with printed volume ({o.volume_cm3:.0f})")
    return issues
