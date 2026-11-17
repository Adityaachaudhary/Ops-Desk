"""Anomaly rules from the brief + impact ranking + 7-day trend context ('memory of past days')."""
from statistics import mean

from .config import LABELS, LINE_ITEMS, Rules

CRIT, WARN, WATCH = "critical", "warning", "watch"


def rng_text(lo, hi):
    return f"{lo:g}% - {hi:g}%"


def trailing(days: list[dict], idx: int, key: str, n: int):
    prev = [d[key] for d in days[max(0, idx - n):idx] if d.get(key) is not None and not d["data_issue"]]
    return mean(prev) if len(prev) >= 3 else None


def detect(days: list[dict], r: Rules) -> list[dict]:
    """days: computed days for ONE fc sorted by date. Returns anomaly dicts."""
    out = []
    for i, d in enumerate(days):
        if d["data_issue"]:
            out.append(_row(d, "Data quality", None, "", "DATA ISSUE", CRIT, 0.0, d["data_issue"]))
            continue
        rev = d["revenue"]
        for k, lab, _, _ in LINE_ITEMS:
            lo, hi = r.ranges[k]
            pct = d[f"{k}_pct"]
            if pct > hi + 1e-9 or pct < lo - 1e-9:
                excess = pct - hi if pct > hi else pct - lo   # signed pp outside range
                status = "ABOVE MAX" if pct > hi else "BELOW MIN"
                trend = trailing(days, i, f"{k}_pct", r.trend_days)
                out.append(_row(d, lab, pct, rng_text(lo, hi), status, WARN, abs(excess) / 100 * rev,
                                f"{lab} = {pct:g}% ({abs(excess):g}pp {'above' if excess > 0 else 'below'} the {rng_text(lo, hi)} range)"
                                + (f"; its trailing {r.trend_days}-day average is {trend:.1f}%" if trend is not None else "")
                                + ("; unusually low cost - check for missing postings" if excess < 0 else ""), excess_pp=excess))
        for which, label in (("cm1", "CM1"), ("cm2", "CM2")):
            pct = d[f"{which}_pct"]
            tlo, thi = (r.cm1_target if which == "cm1" else r.cm2_target)
            lo_lim, hi_lim = r.cm_limits(which)
            if pct < lo_lim:
                st = "CM2 BELOW THRESHOLD (RED)" if which == "cm2" else "CM1 DEVIATION > 3pp BELOW"
                out.append(_row(d, label, pct, rng_text(tlo, thi), st, CRIT, (tlo - pct) / 100 * rev,
                                f"{label} = {pct:g}% is {tlo - pct:g}pp below the target floor of {tlo:g}% (limit {lo_lim:g}%)", excess_pp=pct - tlo))
            elif pct > hi_lim:
                st = "CM2 SUSPICIOUSLY HIGH (BLUE)" if which == "cm2" else "CM1 DEVIATION > 3pp ABOVE"
                out.append(_row(d, label, pct, rng_text(tlo, thi), st, CRIT, (pct - thi) / 100 * rev,
                                f"{label} = {pct:g}% is {pct - thi:g}pp above the target ceiling of {thi:g}% (limit {hi_lim:g}%)"
                                " - verify nothing is under-reported", excess_pp=pct - thi))
            elif which == "cm2" and d["colour"] == "Yellow":
                side = "below" if pct < tlo else "above"
                out.append(_row(d, label, pct, rng_text(tlo, thi), f"CM2 YELLOW ({side} target)", WATCH, abs(pct - (tlo if pct < tlo else thi)) / 100 * rev,
                                f"CM2 = {pct:.1f}% is {side} the {rng_text(tlo, thi)} target but within the +/-{r.margin_tolerance_pp:g}pp tolerance",
                                excess_pp=pct - (tlo if pct < tlo else thi)))
    return out


def _row(d, item, pct, target, status, severity, impact, detail, excess_pp=None):
    return {"fc": d["fc"], "day": d["day"], "line_item": item, "pct": None if pct is None else round(pct, 2), "target_range": target,
            "status": status, "severity": severity, "impact_inr": round(impact, 0), "detail": detail, "excess_pp": excess_pp}
