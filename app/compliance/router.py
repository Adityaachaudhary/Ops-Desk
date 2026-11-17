import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..core import db, jobs
from ..core.config import BASE_DIR, UPLOAD_DIR
from ..core.jobs import UserInputError
from ..core.web import render
from .parser import count_pages, parse_wagesheet, validate_ecr_pdf_kind
from .service import handle  # noqa: F401
from .verifier import STATUSES

CASE_TYPE = "compliance"
router = APIRouter(prefix="/compliance")
SAMPLE_DIR = BASE_DIR / "data" / "sample" / "case2"


def _page(request, error=None):
    return render(request, "compliance.html", active="compliance", error=error, history=jobs.list_jobs(CASE_TYPE, 15),
                  has_sample=SAMPLE_DIR.exists())


@router.get("")
def index(request: Request, new: bool = False):
    cookie = jobs.resume_cookie_name(CASE_TYPE)
    url = jobs.resume_url(request.cookies, CASE_TYPE)
    if not new and url:
        return RedirectResponse(url, status_code=303)
    response = _page(request)
    if request.cookies.get(cookie) and not url:
        response.delete_cookie(cookie)
    return response


def _validate_and_queue(folder: Path, ws: Path, pf: Path, es: Path, tol: str, title: str):
    for p, label in ((pf, "PF ECR"), (es, "ESIC ECR")):
        with open(p, "rb") as f:
            if not f.read(5).startswith(b"%PDF"):
                raise UserInputError(f"The file given as {label} is not a valid PDF (it may be corrupt or a renamed file).")
        if count_pages(p) == 0:
            raise UserInputError(f"The {label} PDF could not be opened (corrupt or password-protected).")
    validate_ecr_pdf_kind(pf, "PF")
    validate_ecr_pdf_kind(es, "ESIC")
    parse_wagesheet(ws)  # fail fast with a readable message on a bad wagesheet
    try:
        from decimal import Decimal
        t = Decimal(tol or "0")
        if t < 0 or t > 100:
            raise ValueError
    except Exception:
        raise UserInputError("Amount tolerance must be a number between 0 and 100 (rupees).")
    meta = {"files": {"wagesheet": str(ws), "pf": str(pf), "esic": str(es), "wagesheet_name": ws.name, "pf_name": pf.name, "esic_name": es.name},
            "tolerance": str(t)}
    return jobs.create_job(CASE_TYPE, title, folder, meta)


@router.post("/upload")
async def upload(request: Request, wagesheet: UploadFile = File(None), pf: UploadFile = File(None), esic: UploadFile = File(None),
                 tolerance: str = Form("0")):
    for f, label in ((wagesheet, "wagesheet (Excel)"), (pf, "PF ECR (PDF)"), (esic, "ESIC ECR (PDF)")):
        if f is None or not f.filename:
            return _page(request, error=f"Please choose the {label} file. All three files are required.")
    try:
        folder, saved = jobs.save_uploads([wagesheet], {".xlsx", ".xls", ".xlsm", ".csv"}, "wagesheet")
        _, pfs = jobs.save_uploads([pf], {".pdf"}, "PF ECR")
        _, ess = jobs.save_uploads([esic], {".pdf"}, "ESIC ECR")
        for p in (pfs[0], ess[0]):  # keep everything of one job in one folder
            shutil.move(str(p), str(folder / p.name))
            shutil.rmtree(p.parent, ignore_errors=True)
        jid = _validate_and_queue(folder, saved[0], folder / pfs[0].name, folder / ess[0].name, tolerance, f"{saved[0].name} vs ECRs")
    except UserInputError as e:
        return _page(request, error=str(e))
    return RedirectResponse(f"/compliance/jobs/{jid}", status_code=303)


@router.post("/sample")
def sample(request: Request):
    if not SAMPLE_DIR.exists():
        return _page(request, error="Sample files are not bundled in this copy.")
    folder = UPLOAD_DIR / ("up_sample_" + uuid.uuid4().hex[:10])
    shutil.copytree(SAMPLE_DIR, folder, dirs_exist_ok=True)
    try:
        ws = next(folder.glob("*.xlsx"))
        pf = next(p for p in folder.glob("*.pdf") if "PF" in p.name.upper() and "ESIC" not in p.name.upper())
        es = next(p for p in folder.glob("*.pdf") if "ESIC" in p.name.upper())
        jid = _validate_and_queue(folder, ws, pf, es, "0", "Sample: Aug-25 wagesheet vs ECRs")
    except (StopIteration, UserInputError) as e:
        return _page(request, error=str(e) if isinstance(e, UserInputError) else "Sample files are incomplete.")
    return RedirectResponse(f"/compliance/jobs/{jid}", status_code=303)


def _job(jid):
    j = jobs.get_job(jid)
    return j if j and j["case_type"] == CASE_TYPE else None


@router.get("/jobs/{jid}")
def job_page(request: Request, jid: str):
    j = _job(jid)
    if not j:
        return render(request, "error.html", status_code=404, title="Job not found", message="This compliance job does not exist.")
    rows = db.query("SELECT * FROM compliance_results WHERE job_id=? ORDER BY idx", (jid,))
    response = render(request, "compliance_job.html", active="compliance", job=j, rows=rows, summary=j["meta"].get("summary"),
                      statuses=STATUSES, events=jobs.events(jid, 8))
    jobs.remember_job(response, jid, CASE_TYPE)
    return response


@router.get("/jobs/{jid}/download")
def download(jid: str):
    j = _job(jid)
    if not j or not j.get("output_path") or not Path(j["output_path"]).exists():
        return Response("Results are not ready yet.", status_code=404)
    return FileResponse(j["output_path"], filename=f"wagesheet_vs_ecr_{jid}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/jobs/{jid}/csv")
def csv_download(jid: str):
    j = _job(jid)
    p = j and j.get("output_path") and Path(j["output_path"]).with_suffix(".csv")
    if not p or not p.exists():
        return Response("Results are not ready yet.", status_code=404)
    return FileResponse(p, filename=f"wagesheet_vs_ecr_{jid}.csv", media_type="text/csv")
