from app.pnl.anomalies import detect
from app.pnl.calculator import colour_for, compute_day
from app.pnl.config import Rules

R = Rules()


def rec(rev=100000, man=26000, pack=6000, pf=11000, rent=16500, eq=6500, oh=11000, **kw):
    return {"fc": "T", "day": "d1", "sort_key": 1.0, "revenue": rev, "manpower": man, "packaging": pack, "power_fuel": pf, "fc_rent": rent,
            "equipment": eq, "overheads": oh, "issues": [], **kw}


def test_colour_bands_and_gaps():
    got = {x: colour_for(x, R) for x in (11.99, 12, 14.9, 14.95, 15, 30, 30.01, 32.9, 32.95, 33, 40)}
    assert got == {11.99: "Red", 12: "Yellow", 14.9: "Yellow", 14.95: "Yellow", 15: "Green", 30: "Green", 30.01: "Yellow", 32.9: "Yellow",
                   32.95: "Yellow", 33: "Blue", 40: "Blue"}


def test_cm_maths():
    d = compute_day(rec(), R)
    assert d["cm1"] == 68000 and d["cm1_pct"] == 68.0 and d["cm2"] == 23000 and d["cm2_pct"] == 23.0 and d["colour"] == "Green"


def test_clean_day_has_no_anomalies():
    assert detect([compute_day(rec(), R)], R) == []


def test_line_item_only_breach():
    d = compute_day(rec(pack=9500), R)  # packaging 9.5% > 7%; CM2 still green
    a = detect([d], R)
    assert d["colour"] == "Green" and [(x["line_item"], x["status"]) for x in a] == [("Packaging", "ABOVE MAX")]


def test_red_and_blue_and_data_issue():
    red = compute_day(rec(pf=15200, man=32800, oh=13000), R)
    assert red["colour"] == "Red" and any(x["status"].startswith("CM2 BELOW") for x in detect([red], R))
    blue = compute_day(rec(man=15000, oh=3000), R)
    assert blue["colour"] == "Blue" and any("SUSPICIOUSLY HIGH" in x["status"] for x in detect([blue], R))
    bad = compute_day(rec(man=None, issues=["manpower: missing"]), R)
    assert bad["colour"] == "Grey" and bad["cm2_pct"] is None and detect([bad], R)[0]["status"] == "DATA ISSUE"
    assert compute_day(rec(rev=0), R)["colour"] == "Grey"
