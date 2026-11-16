"""Deterministic P&L maths. No LLM ever touches a number."""
from .config import LINE_ITEMS, Rules

COST_KEYS = [k for k, *_ in LINE_ITEMS]
REST = [k for k in COST_KEYS if k not in ("manpower", "packaging")]


def colour_for(cm2: float, r: Rules) -> str:
    cm2 = round(cm2, 4)
    if cm2 < r.red_below:
        return "Red"
    if cm2 < r.yellow_low_below:
        return "Yellow"
    if cm2 <= r.green_max:
        return "Green"
    if cm2 < r.blue_from:
        return "Yellow"
    return "Blue"


def compute_day(rec: dict, r: Rules) -> dict:
    out = {"fc": rec["fc"], "day": rec["day"], "sort_key": rec["sort_key"], "revenue": rec["revenue"]}
    for k in COST_KEYS:
        out[k] = rec[k]
        out[f"{k}_pct"] = None
    out.update(cm1=None, cm1_pct=None, cm2=None, cm2_pct=None, colour="Grey", data_issue="", breaches=0)
    issues = list(rec.get("issues", []))
    rev = rec["revenue"]
    if rev is not None and rev <= 0:
        issues.append("revenue is zero or negative")
    if issues:
        out["data_issue"] = "; ".join(dict.fromkeys(issues))
        if rev and rev > 0:  # still show the % for lines that exist, but never a margin
            for k in COST_KEYS:
                if rec[k] is not None:
                    out[f"{k}_pct"] = round(rec[k] / rev * 100, 2)
        return out
    for k in COST_KEYS:
        out[f"{k}_pct"] = round(rec[k] / rev * 100, 2)
    out["cm1"] = rev - rec["manpower"] - rec["packaging"]
    out["cm2"] = out["cm1"] - sum(rec[k] for k in REST)
    out["cm1_pct"] = round(out["cm1"] / rev * 100, 2)
    out["cm2_pct"] = round(out["cm2"] / rev * 100, 2)
    out["colour"] = colour_for(out["cm2"] / rev * 100, r)
    return out


def compute_all(rows: list[dict], r: Rules) -> list[dict]:
    res = [compute_day(x, r) for x in rows]
    res.sort(key=lambda d: (d["fc"], d["sort_key"]))
    return res
