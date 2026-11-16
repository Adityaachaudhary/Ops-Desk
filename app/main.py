"""Operations Intelligence - one FastAPI app, three isolated modules.

Run:  uvicorn app.main:app --port 8000
Isolation: each module is imported inside try/except and registered separately; Case 1 needs Tesseract,
Cases 2 and 3 never import it. If a module fails to import it is shown as disabled, the others keep working."""
import importlib
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .core import db, jobs
from .core.config import WEB_DIR
from .core.web import MODULES, render

log = logging.getLogger("prozo")
app = FastAPI(title="Prozo Operations Intelligence")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

for key in ("parcel", "compliance", "pnl"):
    try:
        mod = importlib.import_module(f"app.{key}.router")
        jobs.register(mod.CASE_TYPE, mod.handle)
        app.include_router(mod.router)
        MODULES[key].update(enabled=True, error="")
    except Exception as e:  # isolation: never take the whole app down
        log.exception("module %s failed to load", key)
        MODULES[key].update(enabled=False, error=f"{type(e).__name__}: {e}")


@app.on_event("startup")
def _startup():
    db.query("SELECT 1")  # creates schema
    jobs.start_workers()


@app.get("/")
def home(request: Request):
    recent = jobs.list_jobs(limit=12)
    stats = {k: db.one("SELECT COUNT(*) n FROM jobs WHERE case_type=?", (k,))["n"] for k in MODULES}
    last_pnl = next((j for j in jobs.list_jobs("pnl", 5) if j["status"].startswith("completed")), None)
    strip = []
    if last_pnl:
        strip = db.query("SELECT fc, day, colour, cm2_pct FROM fc_daily_records WHERE job_id=? AND (fc, sort_key) IN "
                         "(SELECT fc, MAX(sort_key) FROM fc_daily_records WHERE job_id=? GROUP BY fc) ORDER BY fc", (last_pnl["id"], last_pnl["id"]))
    parcel = db.one("SELECT j.id, j.title, SUM(r.status='SUCCESS') ok, COUNT(*) n FROM jobs j JOIN parcel_results r ON r.job_id=j.id "
                    "WHERE j.case_type='parcel' GROUP BY j.id ORDER BY j.created_at DESC LIMIT 1")
    comp = db.one("SELECT id, meta FROM jobs WHERE case_type='compliance' AND status LIKE 'completed%' ORDER BY created_at DESC LIMIT 1")
    comp_sum = (db.jloads(comp["meta"], {}).get("summary") if comp else None)
    return render(request, "home.html", active="home", recent=recent, stats=stats, strip=strip, last_pnl=last_pnl, parcel=parcel,
                  comp=comp, comp_sum=comp_sum)


@app.get("/jobs")
def jobs_page(request: Request):
    return render(request, "jobs.html", active="jobs", recent=jobs.list_jobs(limit=200))


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = jobs.get_job(jid)
    if not j:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return {k: j[k] for k in ("id", "case_type", "status", "total_items", "processed_items", "failed_items", "progress", "message")}


@app.get("/api/jobs/{jid}/events")
def api_events(jid: str):
    return jobs.events(jid)


@app.post("/jobs/{jid}/retry")
def retry(jid: str):
    j = jobs.get_job(jid)
    if j:
        jobs.retry(jid)
    return RedirectResponse(f"/{j['case_type']}/jobs/{jid}" if j else "/", status_code=303)


@app.post("/jobs/{jid}/delete")
def delete(jid: str):
    j = jobs.get_job(jid)
    jobs.delete_job(jid)
    return RedirectResponse(f"/{j['case_type']}" if j else "/jobs", status_code=303)


@app.get("/api/health")
def health():
    return {"status": "ok", "modules": {k: {"enabled": v["enabled"], "error": v["error"]} for k, v in MODULES.items()}}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return render(request, "error.html", status_code=404, title="Page not found", message="That page or job does not exist (it may have been deleted).")


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("unhandled error")
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Unexpected server error"}, status_code=500)
        return render(request, "error.html", status_code=500, title="Something went wrong",
                  message="The request could not be completed. Nothing was lost - please try again or contact the maintainer.",
                  detail=f"{type(exc).__name__}: {exc}")
