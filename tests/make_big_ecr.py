"""Synthetic scale + noise test for Case 2:  python -m tests.make_big_ecr [employees]
Generates a wagesheet and PF / ESIC ECR PDFs (300+ pages at the default size) in the same text layout as the samples,
with KNOWN injected problems, so the verifier can be scored against ground truth. Needs `pip install fpdf2` (test-only)."""
import json
import random
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
from fpdf import FPDF

OUT = Path(__file__).resolve().parents[1] / "data" / "generated" / "big_ecr"
FIRST = "ANIL SUNITA RAKESH PRIYA MANOJ KAVITA DEVENDRA REKHA SANJAY POOJA VIKRAM NEHA RAHUL AMIT RAVI SEEMA GEETA MOHAMMAD SALMAN FATIMA DINESH SURESH RAMESH LAKSHMI".split()
LAST = "KUMAR DEVI VERMA SINGH YADAV KUMARI PRASAD SHARMA PASWAN GUPTA THAKUR MISHRA KHAN ANSARI PATEL IYER REDDY NAIR".split()


def money(x):
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt(n):
    return f"{n:,}"


def make(n=13500, seed=7):
    rnd = random.Random(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    people, seen = [], set()
    while len(people) < n:
        nm = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}" + (f" {rnd.choice(LAST)}" if rnd.random() < .25 else "")
        people.append(nm)
    rows, truth, ws_rows = [], {}, []
    for i, name in enumerate(people, 1):
        wages = rnd.randrange(9000, 21000)
        pf, esic = money(wages * .12), -(-wages * 75 // 10000)
        uan, ip = f"{999900000000 + i}", f"{9900000000 + i}"
        w_name, pf_name, es_name = name, name, name
        pf_amt, es_amt = pf, esic
        pf_in, es_in = True, True
        v = rnd.random()
        if v < .02: pf_in = False; k = ("PF", "Missing")
        elif v < .04: es_in = False; k = ("ESIC", "Missing")
        elif v < .06: pf_amt += rnd.choice([-40, -5, 7, 120]); k = ("PF", "Amount mismatch")
        elif v < .08: es_amt += rnd.choice([-20, -3, 4, 30]); k = ("ESIC", "Amount mismatch")
        elif v < .10: pf_name = rnd.choice(FIRST) + " " + name.split()[-1]; k = ("PF", "Name mismatch") if pf_name != name else None
        elif v < .12: es_name = name.split()[0] + " " + rnd.choice(LAST); k = ("ESIC", "Name mismatch") if es_name != name else None
        elif v < .20: pf_name = " ".join(reversed(name.split())); es_name = name.replace(" ", "  ").lower().title().upper(); k = None  # benign variations
        else: k = None
        if k:
            truth[uan] = k
        rows.append((i, uan, ip, name, pf_name, es_name, pf_amt, es_amt, pf_in, es_in, wages))
        ws_rows.append({"Name": name, "UAN": uan, "IP Number": ip, "Agency": "Synthetic Staffing", "State": "Bihar",
                        "ESIC @ 0.75%": esic, "PF @ 12%": pf})
    pd.DataFrame(ws_rows).to_excel(OUT / "wagesheet_big.xlsx", index=False)
    _pdf(OUT / "PF_ECR_big.pdf", "PF", [r for r in rows if r[8]])
    _pdf(OUT / "ESIC_ECR_big.pdf", "ESIC", [r for r in rows if r[9]])
    (OUT / "ground_truth.json").write_text(json.dumps(truth))
    return len(rows), len(truth)


def _pdf(path, kind, rows, per_page=48):
    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_font("Courier", size=7)
    total = 0
    for pi in range(0, len(rows), per_page):
        pdf.add_page()
        pdf.set_xy(8, 8)
        if pi == 0:
            pdf.multi_cell(0, 3.5, f"{'ELECTRONIC CHALLAN CUM RETURN (ECR)' if kind == 'PF' else 'Contribution History'}\nTotal Members {len(rows)}\n")
        pdf.set_x(8)
        for r in rows[pi:pi + per_page]:
            i, uan, ip, wn, pn, en, pa, ea, *_ = r
            wages = 14000
            if kind == "PF":
                line = f"{i}    {uan} {pn} {pn} {fmt(wages)} {fmt(wages)} {fmt(wages)} {fmt(wages)} {fmt(pa)} {fmt(1000)} {fmt(500)} 0"
            else:
                line = f"{i}    -      {ip} {en}      30      {wages}.00 {ea}.00     -"
            pdf.set_x(8)
            pdf.cell(0, 3.6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 13500
    t = time.time()
    print("generated", make(n), "in", round(time.time() - t, 1), "s ->", OUT)
