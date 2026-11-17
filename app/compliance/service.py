import csv
from decimal import Decimal, InvalidOperation

from ..core import db
from ..core.config import OUTPUT_DIR
from ..core.jobs import UserInputError
from .parser import count_pages, parse_esic_ecr, parse_pf_ecr, parse_wagesheet
from .report import build_workbook
from .verifier import summarise, verify

CSV_COLS = ["idx", "employee", "uan", "ip_number", "agency", "state", "pf_status", "pf_reason", "pf_wagesheet", "pf_ecr", "pf_diff",
            "pf_ecr_name", "esic_status", "esic_reason", "esic_wagesheet", "esic_ecr", "esic_diff", "esic_ecr_name"]


def handle(job, ctx):
    files, jid = job["meta"]["files"], job["id"]
    try:
        tol = Decimal(str(job["meta"].get("tolerance", "0")))
    except InvalidOperation:
        tol = Decimal("0")
    db.execute("DELETE FROM compliance_results WHERE job_id=?", (jid,))
    n_pf, n_es = count_pages(files["pf"]), count_pages(files["esic"])
    ctx.set_total(n_pf + n_es + 3)
    done = 0
    ws = parse_wagesheet(files["wagesheet"])
    done += 1
    ctx.log(f"Wagesheet: {len(ws)} employees")
    ctx.progress(done, message="Reading PF ECR", force=True)

    pf = parse_pf_ecr(files["pf"], on_page=lambda p: ctx.progress(done + p, message=f"PF ECR page {p}/{n_pf}"))
    done += n_pf
    ctx.log(f"PF ECR: {len(pf.rows)} members over {pf.pages} page(s)")
    ctx.progress(done, message="Reading ESIC ECR", force=True)
    es = parse_esic_ecr(files["esic"], on_page=lambda p: ctx.progress(done + p, message=f"ESIC ECR page {p}/{n_es}"))
    done += n_es
    ctx.log(f"ESIC ECR: {len(es.rows)} insured persons over {es.pages} page(s)")
    ctx.progress(done, message="Matching", force=True)

    results, extras = verify(ws, pf, es, tol)
    summary = summarise(results)
    warnings = pf.warnings + es.warnings
    rows = [{**r, "job_id": jid} for r in results]
    db.insert_many("compliance_results", rows)
    out = OUTPUT_DIR / f"compliance_{jid}.xlsx"
    meta = {"Wagesheet file": files["wagesheet_name"], "PF ECR file": files["pf_name"], "ESIC ECR file": files["esic_name"],
            "Amount tolerance (Rs)": str(tol), "PF ECR members parsed": len(pf.rows), "ESIC rows parsed": len(es.rows)}
    build_workbook(out, results, summary, extras, warnings, pf.unparsed[:200] + es.unparsed[:200], meta)
    with open(OUTPUT_DIR / f"compliance_{jid}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    return {"processed": n_pf + n_es + 3, "failed": 0, "output_path": str(out),
            "message": f"{summary['employees']} employees verified - {summary['fully_compliant']} fully compliant, {summary['needs_attention']} need attention",
            "meta": {"summary": summary, "warnings": warnings, "extras": extras, "unparsed_count": len(pf.unparsed) + len(es.unparsed)}}
