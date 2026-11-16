"""Business rules for Case 3 - every number from the brief lives here, nothing is buried in logic."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import DATA_DIR

# key, label, min %, max % of revenue (from 'Ideal FC Criteria' tab of Case Study 3.xlsx)
LINE_ITEMS = [
    ("manpower", "Manpower", 25.0, 27.0),
    ("packaging", "Packaging", 5.0, 7.0),
    ("power_fuel", "Power & Fuel", 10.0, 12.0),
    ("fc_rent", "FC Rent", 15.0, 18.0),
    ("equipment", "Equipment Rentals", 5.0, 8.0),
    ("overheads", "Overheads", 10.0, 12.0),
]
CM1_FIRST = ("manpower", "packaging")  # CM1 = revenue - manpower - packaging
LABELS = {k: l for k, l, _, _ in LINE_ITEMS}


@dataclass
class Rules:
    ranges: dict = field(default_factory=lambda: {k: (lo, hi) for k, _, lo, hi in LINE_ITEMS})
    cm1_target: tuple = (65.0, 70.0)
    cm2_target: tuple = (15.0, 30.0)
    margin_tolerance_pp: float = 3.0
    # How "deviates more than +/-3pp from target" is measured for a target RANGE:
    #   "boundary": distance outside the nearest end of the range  (CM2 -> <12% or >33%, same as the colour bands)
    #   "midpoint": distance from the middle of the range          (stricter)
    deviation_basis: str = "boundary"
    red_below: float = 12.0
    yellow_low_below: float = 15.0   # 12 <= x < 15  -> yellow
    green_max: float = 30.0          # 15 <= x <= 30 -> green
    blue_from: float = 33.0          # x >= 33       -> blue ; 30 < x < 33 -> yellow
    trend_days: int = 7

    def cm_limits(self, which: str):
        lo, hi = self.cm1_target if which == "cm1" else self.cm2_target
        if self.deviation_basis == "midpoint":
            mid = (lo + hi) / 2
            return mid - self.margin_tolerance_pp, mid + self.margin_tolerance_pp
        return lo - self.margin_tolerance_pp, hi + self.margin_tolerance_pp

    def describe(self) -> list[tuple[str, str]]:
        rows = [(LABELS[k], f"{lo:g}% - {hi:g}% of revenue") for k, (lo, hi) in self.ranges.items()]
        rows += [("CM1 target", f"{self.cm1_target[0]:g}% - {self.cm1_target[1]:g}% (flag beyond +/-{self.margin_tolerance_pp:g}pp, basis: {self.deviation_basis})"),
                 ("CM2 target", f"{self.cm2_target[0]:g}% - {self.cm2_target[1]:g}% (flag beyond +/-{self.margin_tolerance_pp:g}pp)"),
                 ("Colour: Red", f"CM2 < {self.red_below:g}%"),
                 ("Colour: Yellow", f"{self.red_below:g}% <= CM2 < {self.yellow_low_below:g}%  or  {self.green_max:g}% < CM2 < {self.blue_from:g}%"),
                 ("Colour: Green", f"{self.yellow_low_below:g}% <= CM2 <= {self.green_max:g}%"),
                 ("Colour: Blue", f"CM2 >= {self.blue_from:g}% (suspicious - check for under-reported costs; exactly 33.0% is treated as Blue)"),
                 ("Colour: Grey", "Data issue (zero/negative revenue, missing or invalid cost) - no margin is computed")]
        return rows


def load_rules() -> Rules:
    """Optional overrides from data/pnl_rules.json (same keys as Rules fields)."""
    r = Rules()
    p = Path(DATA_DIR) / "pnl_rules.json"
    if p.exists():
        try:
            d = json.loads(p.read_text())
            for k, v in d.items():
                if k == "ranges":
                    r.ranges.update({a: tuple(b) for a, b in v.items()})
                elif hasattr(r, k):
                    setattr(r, k, tuple(v) if isinstance(v, list) else v)
        except Exception:
            pass
    return r


def load_managers() -> dict:
    p = Path(DATA_DIR) / "fc_managers.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}
