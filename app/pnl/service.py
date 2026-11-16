import csv
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..core import db
from ..core.config import OUTPUT_DIR
from ..core.jobs import UserInputError
from . import insights as ins
from .anomalies import detect
from .calculator import compute_all
from .config import LABELS, LINE_ITEMS, Rules, load_managers, load_rules
from .llm import polish
from .loader import load_daily, parse_ranges_sheet
from .notifier import build_digest, deliver, save_html

FILL = {"Red": "F4B6B6", "Yellow": "FBE29A", "Green": "CDEBD3", "Blue": "BBD5F3", "Grey": "DADFE2"}
HEAD = PatternFill("solid", fgColor="12353A")


def handle(job, ctx):
    jid, meta = job["id"], job["meta"]
    for t in ("fc_daily_records", "fc_anomalies", "fc_alerts"):
        db.execute(f"DELETE FROM {t} WHERE job_id=?", (jid,))
    rules: Rules = load_rules()
    rows, warnings = load_daily(meta["file"], meta.get("default_fc") or "FC-1")
    ranges = parse_ranges_sheet(meta["file"]) if str(meta["file"]).lower().endswith((".xlsx", ".xlsm", ".xls")) else None
    if ranges:
        rules.ranges.update(ranges)
        ctx.log("Target ranges read from the uploaded workbook's criteria tab")
    days = compute_all(rows, rules)
    by_fc = defaultdict(list)
    for d in days:
        by_fc[d["fc"]].append(d)
    ctx.set_total(len(by_fc) + 2)
    ctx.log(f"{len(rows)} FC-days across {len(by_fc)} FC(s)")
    managers = load_managers()
    anomalies, daily_out, alerts = [], [], []
    done = 0
    for fc, ds in by_fc.items():
        an = detect(ds, rules)
        by_day = defaultdict(list)
        for a in an:
            by_day[a["day"]].append(a)
        for i, d in enumerate(ds):
            day_an = by_day.get(d["day"], [])
            text = ins.day_insight(d, day_an, ds[:i], rules) if day_an or d["colour"] != "Green" else ""
            d["insight"] = text
            d["breaches"] = sum(1 for a in day_an if a["severity"] != "watch")
            line_ai = text
            for a in day_an:
                a["insight"] = a.pop("detail")
        anomalies += an
        # digest for the latest day of each FC (the 'daily' digest); every other day can be rendered on demand
        last = ds[-1]
        day_an = by_day.get(last["day"], [])
        text = last["insight"] or (f"On {last['day']}, {fc}: CM2 = {last['cm2_pct']:.1f}% (within target). No action needed."
                                   if last["cm2_pct"] is not None else last["insight"])
        text = polish(text)
        dg = build_digest(last, day_an, text, managers.get(fc))
        save_html(OUTPUT_DIR / "digests" / jid / f"{fc.replace(' ', '_')}_{last['day']}.html", dg)
        for res in deliver(dg, managers.get(fc), meta.get("send", True)):
            alerts.append({"job_id": jid, "fc": fc, "day": last["day"], "colour": last["colour"], "channel": res["channel"],
                           "recipient": res["recipient"], "status": res["status"], "detail": res["detail"], "subject": dg["subject"],
                           "body_html": dg["html"], "body_text": dg["text"], "created_at": db.now()})
        daily_out += ds
        done += 1
        ctx.progress(done, message=f"Processed {fc}")
    db.insert_many("fc_daily_records", [{"job_id": jid, **{k: d.get(k) for k in (
        "fc", "day", "sort_key", "revenue", "manpower", "packaging", "power_fuel", "fc_rent", "equipment", "overheads", "manpower_pct", "packaging_pct",
        "power_fuel_pct", "fc_rent_pct", "equipment_pct", "overheads_pct", "cm1", "cm1_pct", "cm2", "cm2_pct", "colour", "data_issue", "insight", "breaches")}}
        for d in daily_out])
    db.insert_many("fc_anomalies", [{"job_id": jid, **{k: a.get(k) for k in (
        "fc", "day", "line_item", "pct", "target_range", "status", "impact_inr", "severity", "insight")}} for a in anomalies])
    db.insert_many("fc_alerts", alerts)
    out = OUTPUT_DIR / f"pnl_{jid}.xlsx"
    _workbook(out, daily_out, anomalies, rules, alerts)
    _anomaly_csv(OUTPUT_DIR / f"pnl_anomalies_{jid}.csv", anomalies)
    sev = {s: sum(1 for a in anomalies if a["severity"] == s) for s in ("critical", "warning", "watch")}
    colours = {c: sum(1 for d in daily_out if d["colour"] == c) for c in ("Red", "Yellow", "Green", "Blue", "Grey")}
    sent = sum(1 for a in alerts if a["status"] == "sent")
    return {"processed": len(by_fc) + 2, "failed": 0, "output_path": str(out),
            "message": f"{len(daily_out)} FC-days, {len(anomalies)} anomaly rows, {sent}/{len(by_fc)} digests sent" + ("" if sent else " (dry run - no channel configured)"),
            "meta": {"warnings": warnings, "severity": sev, "colours": colours, "fcs": list(by_fc), "rules": rules.describe(),
                     "ranges_from_file": bool(ranges)}}


def _anomaly_csv(path, anomalies):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "FC", "Line Item", "% of Revenue", "Target Range", "Status", "Severity", "Impact (Rs)", "Insight"])
        for a in sorted(anomalies, key=lambda x: (x["day"], x["fc"], -x["impact_inr"])):
            w.writerow([a["day"], a["fc"], a["line_item"], "" if a["pct"] is None else a["pct"], a["target_range"], a["status"], a["severity"], a["impact_inr"], a["insight"]])


def _style_header(ws):
    for c in ws[1]:
        c.font, c.fill = Font(name="Arial", bold=True, color="FFFFFF"), HEAD
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def _workbook(path, days, anomalies, rules, alerts):
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily P&L"
    H = ["Date", "FC", "Revenue"] + [lab for _, lab, *_ in LINE_ITEMS] + [f"{lab} % of rev" for _, lab, *_ in LINE_ITEMS] + ["CM1", "CM1 %", "CM2", "CM2 %", "Colour", "Data issue", "Insight"]
    ws.append(H)
    for d in sorted(days, key=lambda x: (x["day"], x["fc"])):
        ws.append([d["day"], d["fc"], d["revenue"]] + [d[k] for k, *_ in LINE_ITEMS] + [d[f"{k}_pct"] for k, *_ in LINE_ITEMS]
                  + [d["cm1"], d["cm1_pct"], d["cm2"], d["cm2_pct"], d["colour"], d["data_issue"], d.get("insight", "")])
        ws.cell(ws.max_row, H.index("Colour") + 1).fill = PatternFill("solid", fgColor=FILL[d["colour"]])
    _style_header(ws)
    ws.auto_filter.ref = ws.dimensions
    for i in range(1, len(H) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14 if i < len(H) else 120
    ws2 = wb.create_sheet("Anomalies Log")
    ws2.append(["Date", "FC", "Line Item", "% of Revenue", "Target Range", "Status", "Severity", "Impact (Rs)", "Insight"])
    for a in sorted(anomalies, key=lambda x: (x["day"], x["fc"], -x["impact_inr"])):
        ws2.append([a["day"], a["fc"], a["line_item"], a["pct"], a["target_range"], a["status"], a["severity"], a["impact_inr"], a["insight"]])
    _style_header(ws2)
    ws2.auto_filter.ref = ws2.dimensions
    for i, w in enumerate([12, 22, 18, 13, 14, 30, 10, 12, 110], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws3 = wb.create_sheet("Day Insights")
    ws3.append(["Date", "FC", "Colour", "CM2 %", "Insight"])
    for d in sorted(days, key=lambda x: (x["day"], x["fc"])):
        if d.get("insight"):
            ws3.append([d["day"], d["fc"], d["colour"], d["cm2_pct"], d["insight"]])
            ws3.cell(ws3.max_row, 3).fill = PatternFill("solid", fgColor=FILL[d["colour"]])
    _style_header(ws3)
    for i, w in enumerate([12, 22, 10, 9, 160], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    ws4 = wb.create_sheet("Status Matrix")
    all_days = sorted({d["day"] for d in days})
    ws4.append(["FC"] + all_days)
    for fc in dict.fromkeys(d["fc"] for d in days):
        m = {d["day"]: d for d in days if d["fc"] == fc}
        ws4.append([fc] + [(f"{m[x]['cm2_pct']:.1f}%" if m.get(x) and m[x]["cm2_pct"] is not None else ("n/a" if m.get(x) else "")) for x in all_days])
        for j, x in enumerate(all_days, 2):
            if m.get(x):
                ws4.cell(ws4.max_row, j).fill = PatternFill("solid", fgColor=FILL[m[x]["colour"]])
    _style_header(ws4)
    ws4.column_dimensions["A"].width = 24
    ws5 = wb.create_sheet("Digest Delivery")
    ws5.append(["FC", "Day", "Colour", "Channel", "Recipient", "Status", "Detail", "Subject"])
    for a in alerts:
        ws5.append([a["fc"], a["day"], a["colour"], a["channel"], a["recipient"], a["status"], a["detail"], a["subject"]])
    _style_header(ws5)
    for i, w in enumerate([22, 12, 9, 9, 28, 10, 60, 60], 1):
        ws5.column_dimensions[get_column_letter(i)].width = w
    ws6 = wb.create_sheet("Rules Used")
    ws6.append(["Rule", "Value"])
    for k, v in rules.describe():
        ws6.append([k, v])
    _style_header(ws6)
    ws6.column_dimensions["A"].width = 24
    ws6.column_dimensions["B"].width = 110
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                c.font = Font(name="Arial", size=10)
    wb.save(path)
