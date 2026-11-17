"""Plain-language insight per anomalous FC-day. Template-driven (deterministic, free, reproducible);
an optional LLM may rephrase it but it can never change a number (see llm.py)."""
from .anomalies import CRIT, WARN
from .config import LABELS, Rules

ACTIONS = {
    "manpower": "review overtime, shift rosters and headcount against the day's volume",
    "packaging": "check material wastage, pack-size selection and the packaging rate card",
    "power_fuel": "audit energy / DG usage, meter readings and fuel purchases",
    "fc_rent": "verify the rent invoice and escalation; a low-revenue day also inflates the rent percentage",
    "equipment": "check idle or duplicate equipment rentals and invoice dates",
    "overheads": "review ad-hoc and discretionary overhead postings",
}
KEY_OF = {v: k for k, v in LABELS.items()}


def _inr(x):
    return f"Rs {x:,.0f}"


def day_insight(day: dict, anomalies: list[dict], days_hist: list[dict], r: Rules) -> str:
    """day: computed day, anomalies: this day's anomaly rows, days_hist: prior computed days of the same FC (oldest first)."""
    head = f"On {day['day']}, {day['fc']}"
    if day["data_issue"]:
        return (f"{head}: no margin could be computed because of a data issue - {day['data_issue']}. "
                "Recommend correcting the source data and re-uploading before drawing conclusions.")
    cm2, cm1 = day["cm2_pct"], day["cm1_pct"]
    col = day["colour"]
    state = {"Red": f"CM2 = {cm2:.1f}% (below the {r.red_below:g}% threshold)",
             "Blue": f"CM2 = {cm2:.1f}% (suspiciously high, above {r.blue_from:g}%)",
             "Yellow": f"CM2 = {cm2:.1f}% (yellow band - outside the {r.cm2_target[0]:g}-{r.cm2_target[1]:g}% target)",
             "Green": f"CM2 = {cm2:.1f}% (within target)"}[col]
    lines = sorted([a for a in anomalies if a["line_item"] in KEY_OF.keys() or a["line_item"] in LABELS.values()
                    if a["status"] in ("ABOVE MAX", "BELOW MIN")], key=lambda a: -a["impact_inr"])
    parts = [f"{head}: {state}; CM1 = {cm1:.1f}%."]
    if lines:
        desc = []
        for a in lines:
            word = "above max" if a["status"] == "ABOVE MAX" else "below min"
            desc.append(f"{a['line_item']} = {a['pct']:g}% ({word}, target {a['target_range']})")
        parts.append("Breaches: " + "; ".join(desc) + ".")
        top = ", ".join(f"{i + 1}) {a['line_item']} {abs(a['excess_pp']):g}pp (~{_inr(a['impact_inr'])})" for i, a in enumerate(lines[:3]))
        parts.append(f"Top contributors by impact: {top}.")
    cm_flags = [a for a in anomalies if a["line_item"] in ("CM1", "CM2") and a["severity"] != "watch"]
    for a in cm_flags:
        parts.append(a["detail"] + ".")
    # context from the past: revenue dip inflates fixed-cost %
    prev_rev = [h["revenue"] for h in days_hist[-r.trend_days:] if h.get("revenue") and not h["data_issue"]]
    if len(prev_rev) >= 3:
        avg = sum(prev_rev) / len(prev_rev)
        dip = (avg - day["revenue"]) / avg * 100
        if dip >= 10:
            parts.append(f"Revenue is {dip:.0f}% below its trailing {len(prev_rev)}-day average, which inflates fixed-cost percentages (rent, equipment, overheads).")
    # recommended action
    if col == "Blue":
        action = "Recommend a cost audit for under-reported or missing postings (manpower, power & fuel, rent) before accepting this margin."
    elif lines and col in ("Red", "Yellow", "Green"):
        above = [a for a in lines if a["status"] == "ABOVE MAX"][:2]
        if above:
            acts = "; ".join(f"{a['line_item']}: {ACTIONS[KEY_OF[a['line_item']]]}" for a in above)
            action = ("Recommend a cost audit - " if col == "Red" else "Recommended action - ") + acts + "."
        else:
            action = "Costs are below their expected ranges; confirm all invoices for the day have been posted."
    elif col == "Yellow":
        action = "Monitor closely; compare today's cost mix with the previous 7 days."
    else:
        return " ".join(parts) + " No action needed."
    parts.append(action)
    return " ".join(parts)
