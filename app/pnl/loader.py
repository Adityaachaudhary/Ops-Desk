"""Read CSV/Excel of daily FC revenue + cost lines. Tolerant about header position, column spelling,
optional FC / Date columns. Bad input -> UserInputError with a message a non-technical user can act on."""
import re
from datetime import date, datetime

from ..core.jobs import UserInputError
from ..core.tabular import read_sheets
from .config import LINE_ITEMS

ALIASES = {
    "revenue": [r"revenue", r"sales", r"turnover", r"income"],
    "manpower": [r"manpower", r"man power", r"labou?r", r"wages", r"salary"],
    "packaging": [r"packaging", r"packing", r"packing material"],
    "power_fuel": [r"power", r"fuel", r"electricity"],
    "fc_rent": [r"fc rent", r"rent$", r"^rent"],
    "equipment": [r"equipment", r"rental"],
    "overheads": [r"overhead"],
}
FC_HDR = re.compile(r"^(fc|fc name|fc code|centre|center|warehouse|site|facility|location)$", re.I)
DAY_HDR = re.compile(r"^(date|day|dt|business date)$", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%d-%m-%y", "%d/%m/%y")
EMPTY = ("", "nan", "None", "NaT")


def _map_header(h) -> str | None:
    h = re.sub(r"\s+", " ", str(h).strip().lower().replace("&", "and"))
    if not h or h in ("nan", "none"):
        return None
    if FC_HDR.match(h):
        return "fc"
    if DAY_HDR.match(h):
        return "day"
    # equipment before rent so 'Equipment Rentals' is not read as FC Rent
    for key in ("revenue", "manpower", "packaging", "power_fuel", "equipment", "fc_rent", "overheads"):
        if any(re.search(p, h) for p in ALIASES[key]):
            return key
    return None


def parse_ranges_sheet(path) -> dict | None:
    """If the workbook has an 'Expense Line Item | Range (as % of Revenue)' table (like the brief's first tab), use it."""
    try:
        sheets = read_sheets(path)
    except UserInputError:
        return None
    for _, rows in sheets:
        found = {}
        for row in rows:
            vals = [str(v) for v in row if str(v) not in EMPTY]
            for i, v in enumerate(vals):
                key = _map_header(v)
                if key and key != "revenue" and i + 1 < len(vals):
                    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*%", vals[i + 1])
                    if m:
                        found[key] = (float(m.group(1)), float(m.group(2)))
        if len(found) >= 4:
            return found
    return None


def load_daily(path, default_fc: str = "FC-1"):
    """Returns (rows, warnings). rows: dict(fc, day, sort_key, revenue, <6 cost keys>, issues[list])."""
    best = None
    for _, raw in read_sheets(path):
        for i in range(min(30, len(raw))):
            m = {}
            for j, h in enumerate(raw[i]):
                k = _map_header(h)
                if k and k not in m:
                    m[k] = j
            if "revenue" in m and sum(k in m for k, *_ in LINE_ITEMS) >= 4:
                best = (raw, i, m)
                break
        if best:
            break
    if best is None:
        raise UserInputError("Could not find the data table. Expected a header row with: Day/Date, Revenue, Manpower, Packaging, "
                             "Power & Fuel, FC Rent, Equipment Rentals, Overheads (and optionally an FC column).")
    raw, hdr, cols = best
    missing = [lab for k, lab, *_ in LINE_ITEMS if k not in cols]
    if missing:
        raise UserInputError("These required columns are missing: " + ", ".join(missing) + ".")
    warnings, rows, seen = [], [], {}
    for i in range(hdr + 1, len(raw)):
        r = raw[i]
        if all(str(v) in EMPTY for v in r):
            continue
        g = lambda k: (r[cols[k]] if k in cols and cols[k] < len(r) else None)
        day_raw = g("day")
        if "day" in cols and str(day_raw) in EMPTY + ("None",):
            warnings.append(f"Row {i + 1}: no day/date value - row skipped.")
            continue
        label, sort_key = _day(day_raw, i - hdr)
        fc_val = g("fc")
        fc = str(fc_val).strip() if "fc" in cols and str(fc_val) not in EMPTY else default_fc
        rec = {"fc": fc or default_fc, "day": label, "sort_key": sort_key, "issues": [], "source_row": i + 1}
        for k in ("revenue", "manpower", "packaging", "power_fuel", "fc_rent", "equipment", "overheads"):
            v, issue = _num(g(k))
            rec[k] = v
            if issue:
                rec["issues"].append(f"{k.replace('_', ' ')}: {issue}")
        key = (rec["fc"], rec["day"])
        if key in seen:
            warnings.append(f"Duplicate row for {rec['fc']} on {rec['day']} (row {i + 1}); the later row replaces the earlier one.")
            rows[seen[key]] = rec
        else:
            seen[key] = len(rows)
            rows.append(rec)
    if not rows:
        raise UserInputError("The table has a header but no data rows.")
    return rows, warnings


def _num(v):
    if v is None or str(v) in EMPTY:
        return None, "missing"
    s = str(v).replace(",", "").replace("₹", "").replace("%", "").strip()
    try:
        x = float(s)
    except ValueError:
        return None, f"not a number ('{v}')"
    if x != x:
        return None, "missing"
    if x < 0:
        return x, "negative value"
    return x, None


def _day(v, ordinal):
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, date):
        return v.isoformat(), float(v.toordinal())
    s = str(v).strip()
    try:
        x = float(s)
        if x == int(x) and x < 100000:
            return f"Day {int(x)}", float(x)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.isoformat(), float(d.toordinal())
        except ValueError:
            continue
    return s or f"Row {ordinal}", float(ordinal)