"""Deterministic reconciliation: wagesheet row -> PF ECR (by UAN) and ESIC ECR (by IP Number).
Identifier match is EXACT (string). Names are then compared tolerantly; amounts with Decimal and an exact-match default (an explicitly approved tolerance can be configured).
Ambiguity (duplicates, unreadable name column) -> 'Manual review'."""
from collections import Counter, defaultdict
from decimal import Decimal

from .matcher import compare_names

MATCHED, NAME_MM, AMT_MM, MISSING = "Matched", "Name mismatch", "Amount mismatch", "Missing"
REVIEW, NA = "Manual review", "Not applicable"
STATUSES = [MATCHED, NAME_MM, AMT_MM, MISSING, REVIEW, NA]


def fmt(d: Decimal | None) -> str:
    if d is None:
        return ""
    d = d.normalize()
    s = format(d, "f")
    return s


def _index(rows):
    idx = defaultdict(list)
    for r in rows:
        idx[r["id"]].append(r)
    return idx


def _check(kind, ident, ws_name, ws_amt, ws_raw, ecr_idx, ws_dups, tol, id_label, amt_label):
    base = {"status": "", "reason": "", "ws_amount": fmt(ws_amt), "ecr_amount": "", "diff": "", "ecr_name": "", "score": None}
    if not ident:
        if ws_amt is None or ws_amt == 0:
            return {**base, "status": NA, "reason": f"No {id_label} and no {amt_label} deduction in the wagesheet - nothing to deposit "
                                                    f"(e.g. employee outside {kind} coverage)."}
        return {**base, "status": MISSING, "reason": f"{id_label} is blank in the wagesheet but {amt_label} {fmt(ws_amt)} was deducted."}
    hits = ecr_idx.get(ident, [])
    if not hits:
        return {**base, "status": MISSING, "reason": f"{id_label} {ident} does not appear in the {kind} ECR at all."}
    if ws_dups > 1:
        return {**base, "status": REVIEW, "ecr_name": hits[0]["name"], "ecr_amount": fmt(hits[0]["amount"]),
                "reason": f"{id_label} {ident} appears {ws_dups} times in the wagesheet - cannot attribute the ECR row safely."}
    if len(hits) > 1:
        return {**base, "status": REVIEW, "ecr_name": hits[0]["name"],
                "reason": f"{id_label} {ident} appears {len(hits)} times in the {kind} ECR (amounts: "
                          f"{', '.join(fmt(h['amount']) for h in hits)}) - verify manually."}
    h = hits[0]
    base.update(ecr_name=h["name"], ecr_amount=fmt(h["amount"]))
    if h.get("name_ambiguous"):
        return {**base, "status": REVIEW, "reason": f"Could not separate the two name columns in the ECR row ('{h['name']}'); verify name manually."}
    if h.get("ocr_ambiguous"):
        return {**base, "status": REVIEW, "reason": "OCR was uncertain about a PF ECR numeric field; verify the row manually."}
    if ws_amt is None:
        return {**base, "status": REVIEW, "reason": f"Wagesheet {amt_label} amount is blank/unreadable ('{ws_raw}'); ECR shows {fmt(h['amount'])}."}
    nr = compare_names(ws_name, h["name"])
    base["score"] = nr.score
    diff = (h["amount"] - ws_amt) if h["amount"] is not None else None
    base["diff"] = fmt(diff)
    amt_ok = diff is not None and abs(diff) <= tol
    amt_txt = (f"{amt_label} wagesheet {fmt(ws_amt)} vs ECR {fmt(h['amount'])} (difference {fmt(diff)})")
    if not nr.ok:
        extra = "" if amt_ok else f" Also {amt_txt}."
        return {**base, "status": NAME_MM,
                "reason": f"{id_label} found but name differs: wagesheet '{ws_name}' vs ECR '{h['name']}' "
                          f"({nr.note}, similarity {nr.score:.0f}%).{extra}"}
    if not amt_ok:
        return {**base, "status": AMT_MM, "reason": f"{id_label} and name match ({nr.note}); {amt_txt}."}
    tail = "" if nr.note in ("exact", "same words") else f"; name accepted as {nr.note}"
    rnd = "" if diff == 0 else f"; within Rs {fmt(tol)} rounding tolerance (diff {fmt(diff)})"
    return {**base, "status": MATCHED, "reason": f"{id_label} found{tail}; {amt_label} {fmt(ws_amt)} vs ECR {fmt(h['amount'])}{rnd}."}


def verify(ws_rows, pf, esic, tolerance: Decimal = Decimal("0")):
    pf_idx, es_idx = _index(pf.rows), _index(esic.rows)
    uan_count = Counter(r["uan"] for r in ws_rows if r["uan"])
    ip_count = Counter(r["ip"] for r in ws_rows if r["ip"])
    out = []
    for i, r in enumerate(ws_rows, 1):
        p = _check("PF", r["uan"], r["name"], r["pf_amt"], r["pf_raw"], pf_idx, uan_count.get(r["uan"], 0), tolerance,
                   "UAN", "PF (EE share)")
        e = _check("ESIC", r["ip"], r["name"], r["esic_amt"], r["esic_raw"], es_idx, ip_count.get(r["ip"], 0), tolerance,
                   "IP Number", "ESIC (IP contribution)")
        out.append({"idx": i, "employee": r["name"], "uan": r["uan"], "ip_number": r["ip"], "agency": r["agency"], "state": r["state"],
                    "pf_status": p["status"], "pf_reason": p["reason"], "pf_wagesheet": p["ws_amount"], "pf_ecr": p["ecr_amount"],
                    "pf_diff": p["diff"], "pf_ecr_name": p["ecr_name"], "pf_score": p["score"],
                    "esic_status": e["status"], "esic_reason": e["reason"], "esic_wagesheet": e["ws_amount"], "esic_ecr": e["ecr_amount"],
                    "esic_diff": e["diff"], "esic_ecr_name": e["ecr_name"], "esic_score": e["score"]})
    ws_uans, ws_ips = {r["uan"] for r in ws_rows}, {r["ip"] for r in ws_rows}
    extras = {
        "PF": [{"id": r["id"], "name": r["name"], "amount": fmt(r["amount"])} for r in pf.rows if r["id"] not in ws_uans],
        "ESIC": [{"id": r["id"], "name": r["name"], "amount": fmt(r["amount"])} for r in esic.rows if r["id"] not in ws_ips],
    }
    return out, extras


def summarise(results):
    s = {"employees": len(results)}
    for k in ("pf", "esic"):
        c = Counter(r[f"{k}_status"] for r in results)
        s[k] = {st: c.get(st, 0) for st in STATUSES}
    both = sum(1 for r in results if r["pf_status"] in (MATCHED, NA) and r["esic_status"] in (MATCHED, NA))
    s["fully_compliant"] = both
    s["needs_attention"] = len(results) - both
    return s
