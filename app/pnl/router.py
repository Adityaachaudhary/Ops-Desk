import os
import uuid
import shutil
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from ..core import db, jobs
from ..core.config import BASE_DIR, UPLOAD_DIR
from ..core.jobs import UserInputError
from ..core.web import render
from .charts import cm2_chart
from .config import LINE_ITEMS, load_managers
from .loader import load_daily
from .notifier import build_digest, channels_configured, deliver
from .service import handle  # noqa: F401

CASE_TYPE = "pnl"
router = APIRouter(prefix="/pnl")
SAMPLE = BASE_DIR / "data" / "sample" / "case3" / "case3_extended_sample.csv"
KEYS = [k for k, *_ in LINE_ITEMS]


def _page(request, error=None):
    return render(request, "pnl.html", active="pnl", error=error, history=jobs.list_jobs(CASE_TYPE, 15), has_sample=SAMPLE.exists(),
                  smtp=bool(os.getenv("SMTP_HOST")), slack=bool(os.getenv("SLACK_WEBHOOK_URL")),
                  inbox_dir=os.getenv("PROZO_PNL_INBOX_DIR", "").strip())


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


def _queue(file: Path, folder: Path, default_fc: str, send: bool, title: str):
    rows, _ = load_daily(file, default_fc or "FC-1")  # fail fast with a readable message
    fcs = len({r["fc"] for r in rows})
    return jobs.create_job(CASE_TYPE, title, folder, {"file": str(file), "default_fc": default_fc or "FC-1", "send": send}, total=fcs + 2)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(None), default_fc: str = Form(""), send: str = Form("")):
    try:
        folder, saved = jobs.save_uploads([file] if file is not None else [], {".csv", ".xlsx", ".xls", ".xlsm", ".tsv"}, "P&L file")
        jid = _queue(saved[0], folder, default_fc.strip(), bool(send), saved[0].name)
    except UserInputError as e:
        return _page(request, error=str(e))
    return RedirectResponse(f"/pnl/jobs/{jid}", status_code=303)


@router.post("/sample")
def sample(request: Request, send: str = Form("")):
    if not SAMPLE.exists():
        return _page(request, error="Sample data is not bundled in this copy.")
    folder = UPLOAD_DIR / ("up_sample_" + uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    dest = folder / SAMPLE.name
    shutil.copy(SAMPLE, dest)
    try:
        jid = _queue(dest, folder, "", bool(send), "Sample: 5 FCs x 30 days (extended)")
    except UserInputError as e:
        return _page(request, error=str(e))
    return RedirectResponse(f"/pnl/jobs/{jid}", status_code=303)


def _job(jid):
    j = jobs.get_job(jid)
    return j if j and j["case_type"] == CASE_TYPE else None


def _overrides():
    d = defaultdict(list)
    for o in db.query("SELECT * FROM overrides ORDER BY id DESC"):
        d[(o["fc"], o["day"], o["line_item"])].append(o)
    return d


@router.get("/jobs/{jid}")
def job_page(request: Request, jid: str, tab: str = "dashboard", fc: str = "", severity: str = ""):
    j = _job(jid)
    if not j:
        return render(request, "error.html", status_code=404, title="Job not found", message="This P&L job does not exist.")
    ctx = dict(job=j, tab=tab, events=jobs.events(jid, 8), kpi=j["meta"])
    if j["status"].startswith("completed"):
        daily = db.query("SELECT * FROM fc_daily_records WHERE job_id=? ORDER BY fc, sort_key", (jid,))
        fcs = list(dict.fromkeys(d["fc"] for d in daily))
        by_fc = {f: [d for d in daily if d["fc"] == f] for f in fcs}
        days = sorted({(d["sort_key"], d["day"]) for d in daily})
        grid = {f: {d["day"]: d for d in v} for f, v in by_fc.items()}
        latest = {f: v[-1] for f, v in by_fc.items()}
        alerts = {a["fc"]: a for a in db.query("SELECT * FROM fc_alerts WHERE job_id=? ORDER BY id", (jid,))}
        anomalies = db.query("SELECT * FROM fc_anomalies WHERE job_id=? ORDER BY day DESC, fc, impact_inr DESC", (jid,))
        if fc:
            anomalies = [a for a in anomalies if a["fc"] == fc]
        if severity:
            anomalies = [a for a in anomalies if a["severity"] == severity]
        sel = fc if fc in by_fc else fcs[0]
        ctx.update(daily=by_fc[sel], fcs=fcs, grid=grid, day_cols=[d for _, d in days], latest=latest, alerts=alerts, anomalies=anomalies[:600],
                   n_anom=len(anomalies), sel=sel, fc_filter=fc, severity=severity, overrides=_overrides(), keys=LINE_ITEMS,
                   charts={f: cm2_chart(v) for f, v in by_fc.items()}, all_alerts=db.query("SELECT * FROM fc_alerts WHERE job_id=? ORDER BY id", (jid,)),
                   managers=load_managers(), rules=j["meta"].get("rules", []),
                   ranges={k: (lo, hi) for k, _, lo, hi in LINE_ITEMS}, day_options=[d for _, d in days])
    response = render(request, "pnl_job.html", active="pnl", **ctx)
    jobs.remember_job(response, jid, CASE_TYPE)
    return response


def _digest_for(jid, fc, day):
    d = db.one("SELECT * FROM fc_daily_records WHERE job_id=? AND fc=? AND day=?", (jid, fc, day))
    if not d:
        return None, None
    an = db.query("SELECT * FROM fc_anomalies WHERE job_id=? AND fc=? AND day=?", (jid, fc, day))
    text = d["insight"] or (f"On {day}, {fc}: CM2 = {d['cm2_pct']:.1f}% (within target). No action needed." if d["cm2_pct"] is not None else "Data issue.")
    return d, build_digest(d, an, text, load_managers().get(fc))


@router.get("/jobs/{jid}/digest", response_class=HTMLResponse)
def digest_preview(jid: str, fc: str, day: str):
    d, dg = _digest_for(jid, fc, day)
    if not d:
        return HTMLResponse("<p>No data for that FC/day.</p>", status_code=404)
    return HTMLResponse(f"<div class='digest-subject'><b>Subject:</b> {dg['subject']}</div>{dg['html']}")


@router.post("/jobs/{jid}/digest/send")
def digest_send(jid: str, fc: str = Form(...), day: str = Form(...)):
    d, dg = _digest_for(jid, fc, day)
    if d:
        mgr = load_managers().get(fc)
        for res in deliver(dg, mgr, True):
            db.insert_many("fc_alerts", [{"job_id": jid, "fc": fc, "day": day, "colour": d["colour"], "channel": res["channel"],
                                          "recipient": res["recipient"], "status": res["status"], "detail": res["detail"], "subject": dg["subject"],
                                          "body_html": dg["html"], "body_text": dg["text"], "created_at": db.now()}])
        db.audit("send_digest", f"{jid} {fc} {day}")
    return RedirectResponse(f"/pnl/jobs/{jid}?tab=digests", status_code=303)


@router.post("/override")
def override(job_id: str = Form(...), fc: str = Form(...), day: str = Form(...), line_item: str = Form(...), note: str = Form(""),
             actor: str = Form("manager")):
    note = note.strip()[:500]
    if note:
        db.execute("INSERT INTO overrides(ts,fc,day,line_item,note,actor) VALUES(?,?,?,?,?,?)", (db.now(), fc, day, line_item, note, actor.strip()[:60] or "manager"))
        db.audit("override", f"{fc} {day} {line_item}: {note}", actor)
    return RedirectResponse(f"/pnl/jobs/{job_id}?tab=anomalies#a-{abs(hash((fc, day, line_item))) % 10**8}", status_code=303)


@router.get("/jobs/{jid}/download")
def download(jid: str):
    j = _job(jid)
    if not j or not j.get("output_path") or not Path(j["output_path"]).exists():
        return Response("Results are not ready yet.", status_code=404)
    return FileResponse(j["output_path"], filename=f"fc_pnl_{jid}.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/jobs/{jid}/anomalies.csv")
def anomalies_csv(jid: str):
    p = OUT = Path(__import__("app.core.config", fromlist=["OUTPUT_DIR"]).OUTPUT_DIR) / f"pnl_anomalies_{jid}.csv"
    if not p.exists():
        return Response("Results are not ready yet.", status_code=404)
    return FileResponse(p, filename=f"fc_anomalies_{jid}.csv", media_type="text/csv")
