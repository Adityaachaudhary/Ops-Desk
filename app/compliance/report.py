from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .verifier import STATUSES

FILL = {"Matched": "D7F0DD", "Name mismatch": "FFE3B3", "Amount mismatch": "FFD0C8", "Missing": "F6B8B8",
        "Manual review": "DCD3F5", "Not applicable": "E6E9EC"}
HEAD = PatternFill("solid", fgColor="12353A")
F = "Arial"


def _sheet(wb, title, headers, rows, widths=None, status_cols=()):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = Font(name=F, bold=True, color="FFFFFF"), HEAD
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10)
            c.alignment = Alignment(vertical="top", wrap_text=c.column_letter in ("F", "G") and False)
        for si in status_cols:
            cell = row[si]
            if cell.value in FILL:
                cell.fill = PatternFill("solid", fgColor=FILL[cell.value])
    for i, h in enumerate(headers, 1):
        w = (widths or {}).get(h, max(12, min(60, len(str(h)) + 4)))
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def build_workbook(path, results, summary, extras, warnings, unparsed, meta):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"], ws["A1"].font = "Wagesheet vs ECR verification - summary", Font(name=F, bold=True, size=14)
    ws.append([])
    ws.append(["Employees in wagesheet", summary["employees"]])
    ws.append(["Fully compliant (PF and ESIC matched / not applicable)", summary["fully_compliant"]])
    ws.append(["Needing attention", summary["needs_attention"]])
    ws.append([])
    ws.append(["Status", "PF", "ESIC"])
    for c in ws[ws.max_row]:
        c.font, c.fill = Font(name=F, bold=True, color="FFFFFF"), HEAD
    for st in STATUSES:
        ws.append([st, summary["pf"][st], summary["esic"][st]])
        ws.cell(ws.max_row, 1).fill = PatternFill("solid", fgColor=FILL[st])
    ws.append([])
    ws.append(["Run settings"])
    ws.cell(ws.max_row, 1).font = Font(name=F, bold=True)
    for k, v in meta.items():
        ws.append([k, v])
    if warnings:
        ws.append([])
        ws.append(["Warnings"])
        ws.cell(ws.max_row, 1).font = Font(name=F, bold=True, color="B00020")
        for w in warnings:
            ws.append([w])
    for row in ws.iter_rows(min_row=3):
        for c in row:
            if c.font.name != F:
                c.font = Font(name=F, size=10)
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 14

    H = ["#", "Employee", "UAN", "IP Number", "Agency", "State",
         "PF status", "PF reason", "PF wagesheet", "PF ECR (EE share)", "PF difference", "PF ECR name",
         "ESIC status", "ESIC reason", "ESIC wagesheet", "ESIC ECR (IP contrib.)", "ESIC difference", "ESIC ECR name"]
    rows = [[r["idx"], r["employee"], r["uan"], r["ip_number"], r["agency"], r["state"],
             r["pf_status"], r["pf_reason"], r["pf_wagesheet"], r["pf_ecr"], r["pf_diff"], r["pf_ecr_name"],
             r["esic_status"], r["esic_reason"], r["esic_wagesheet"], r["esic_ecr"], r["esic_diff"], r["esic_ecr_name"]]
            for r in results]
    widths = {"Employee": 24, "UAN": 16, "IP Number": 14, "Agency": 22, "PF reason": 70, "ESIC reason": 70}
    _sheet(wb, "Employee Verification", H, rows, widths, status_cols=(6, 12))
    pf_x = [[r["idx"], r["employee"], r["uan"], r["pf_status"], r["pf_wagesheet"], r["pf_ecr"], r["pf_diff"], r["pf_ecr_name"], r["pf_reason"]]
            for r in results if r["pf_status"] not in ("Matched", "Not applicable")]
    _sheet(wb, "PF Exceptions", ["#", "Employee", "UAN", "Status", "Wagesheet PF", "ECR EE share", "Difference", "ECR name", "Reason"],
           pf_x, {"Employee": 24, "UAN": 16, "ECR name": 24, "Reason": 90}, status_cols=(3,))
    es_x = [[r["idx"], r["employee"], r["ip_number"], r["esic_status"], r["esic_wagesheet"], r["esic_ecr"], r["esic_diff"], r["esic_ecr_name"], r["esic_reason"]]
            for r in results if r["esic_status"] not in ("Matched", "Not applicable")]
    _sheet(wb, "ESIC Exceptions", ["#", "Employee", "IP Number", "Status", "Wagesheet ESIC", "ECR IP contribution", "Difference", "ECR name", "Reason"],
           es_x, {"Employee": 24, "IP Number": 14, "ECR name": 24, "Reason": 90}, status_cols=(3,))
    ex = [["PF", x["id"], x["name"], x["amount"]] for x in extras["PF"]] + [["ESIC", x["id"], x["name"], x["amount"]] for x in extras["ESIC"]]
    _sheet(wb, "In ECR not in Wagesheet", ["ECR", "Identifier", "Name in ECR", "Amount"], ex, {"Identifier": 20, "Name in ECR": 30})
    _sheet(wb, "Parse Warnings", ["Type", "Detail"], [["warning", w] for w in warnings] + [["unparsed line", u] for u in unparsed],
           {"Detail": 120})
    wb.save(path)
