"""Builds the extended sample: 4 synthetic FCs x 30 days with guaranteed scenarios + the first 30 days of the provided data.
Deterministic (seeded). Run:  python -m app.pnl.datagen"""
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import LINE_ITEMS

START = date(2026, 10, 1)
COLS = ["Date", "FC", "Revenue", "Manpower", "Packaging", "Power & Fuel", "FC Rent", "Equipment Rentals", "Overheads"]
KEYS = [k for k, *_ in LINE_ITEMS]

# fc -> {day_index: {line_key: pct override}}  (None as pct = blank cell -> data issue)
SCENARIOS = {
    "FC Gurugram": {29: {"power_fuel": 15.2, "manpower": 32.8, "packaging": 8.0, "overheads": 13.0}, 19: {}},   # latest day = CM2 breach (red); day 19 = clean
    "FC Bhiwandi": {7: {"packaging": 9.4},                                                                   # line-item-only breach (CM2 stays green)
                    29: {"manpower": 20.0, "overheads": 7.0, "power_fuel": 8.5, "fc_rent": 14.0}},           # latest day = suspiciously high CM2 (blue)
    "FC Bengaluru": {29: {"fc_rent": 21.0, "equipment": 9.0, "manpower": 29.0},                                # latest day = yellow (low side)
                     17: {"manpower": 22.0, "overheads": 9.0, "power_fuel": 9.0}},                           # yellow (high side)
    "FC Kolkata": {14: {"manpower": None}, 29: {"power_fuel": 13.1}},                                        # missing cost cell; latest day = line-item-only breach
}


def generate(seed: int = 42) -> pd.DataFrame:
    rnd = random.Random(seed)
    rows = []
    for fc, sc in SCENARIOS.items():
        base = {k: rnd.uniform(lo + 0.3 * (hi - lo), hi - 0.3 * (hi - lo)) for k, _, lo, hi in LINE_ITEMS}
        for i in range(30):
            rev = round(rnd.uniform(88000, 122000))
            pct = {k: min(max(base[k] + rnd.uniform(-0.4, 0.4), lo + 0.1), hi - 0.1) for k, _, lo, hi in LINE_ITEMS}
            pct.update(sc.get(i, {}))
            row = [(START + timedelta(days=i)).isoformat(), fc, rev]
            for k in KEYS:
                row.append(None if pct[k] is None else round(rev * pct[k] / 100))
            rows.append(row)
    return pd.DataFrame(rows, columns=COLS)


def with_provided(provided_xlsx: Path | None) -> pd.DataFrame:
    df = generate()
    if provided_xlsx and Path(provided_xlsx).exists():
        from .loader import load_daily
        recs, _ = load_daily(provided_xlsx, "FC Sample (provided data)")
        extra = []
        for i, r in enumerate(recs[:30]):
            extra.append([(START + timedelta(days=i)).isoformat(), r["fc"], r["revenue"], r["manpower"], r["packaging"], r["power_fuel"],
                          r["fc_rent"], r["equipment"], r["overheads"]])
        df = pd.concat([df, pd.DataFrame(extra, columns=COLS)], ignore_index=True)
    return df


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[2] / "data" / "sample" / "case3"
    out.mkdir(parents=True, exist_ok=True)
    src = out / "Case_Study_3.xlsx"
    d = with_provided(src)
    d.to_csv(out / "case3_extended_sample.csv", index=False)
    print("wrote", out / "case3_extended_sample.csv", len(d), "rows")
