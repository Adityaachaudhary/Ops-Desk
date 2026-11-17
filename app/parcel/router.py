import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from ..core import db, jobs
from ..core.config import BASE_DIR, UPLOAD_DIR
from ..core.jobs import UserInputError
from ..core.web import render
from .service import IMG_EXT, expand_zips, handle, list_images  # noqa: F401  (handle is registered by main)

CASE_TYPE = "parcel"
router = APIRouter(prefix="/parcel")
SAMPLE_DIR = BASE_DIR / "data" / "sample" / "case1"
STATUS_ORDER = ["SUCCESS", "PARTIAL_EXTRACTION", "LOW_CONFIDENCE", "LABEL_UNREADABLE", "NO_PARCEL", "MULTIPLE_PARCELS", "PARTIAL_PARCEL", "LABEL_BLOCKED", "INVALID_IMAGE"]
STATUS_HELP = {
    "SUCCESS": "AWB, weight and dimensions read cleanly",
    "PARTIAL_EXTRACTION": "AWB read; weight and/or dimensions missing",
    "LOW_CONFIDENCE": "Fields read but OCR confidence or digit ambiguity says verify",
    "LABEL_UNREADABLE": "No AWB could be read from the frame",
    "NO_PARCEL": "No parcel found in the frame",
    "MULTIPLE_PARCELS": "More than one parcel in the frame",
    "PARTIAL_PARCEL": "Parcel cut off at the frame edge",
    "LABEL_BLOCKED": "Parcel present but label covered",
    "INVALID_IMAGE": "Corrupt / unreadable image file",
}


def ocr_status():
    try:
        from . import ocr
        ocr.ensure_ocr()
        return True, ""
    except Exception as e:
        return False, str(e)


def _page(request, error=None, **kw):
    ok, msg = ocr_status()
    return render(request, "parcel.html", active="parcel", error=error, ocr_ok=ok, ocr_msg=msg, history=jobs.list_jobs(CASE_TYPE, 15),
                  has_sample=SAMPLE_DIR.exists() and any(SAMPLE_DIR.iterdir()), **kw)


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


def _queue(folder: Path, title: str) -> str:
    n = len(list_images(folder))
    if n == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise UserInputError("No JPG/PNG images were found in the upload.")
    return jobs.create_job(CASE_TYPE, title, folder, total=n)


@router.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(default=[])):
    ok, msg = ocr_status()
    if not ok:
        return _page(request, error=msg)
    try:
        folder, _ = jobs.save_uploads(files, IMG_EXT | {".zip"}, "images")
        expand_zips(folder)
        jid = _queue(folder, f"{len(list_images(folder))} images")
    except UserInputError as e:
        return _page(request, error=str(e))
    return RedirectResponse(f"/parcel/jobs/{jid}", status_code=303)


@router.post("/sample")
def sample(request: Request):
    ok, msg = ocr_status()
    if not ok:
        return _page(request, error=msg)
    if not SAMPLE_DIR.exists():
        return _page(request, error="Sample images are not bundled in this copy.")
    folder = UPLOAD_DIR / ("up_sample_" + uuid.uuid4().hex[:10])
    shutil.copytree(SAMPLE_DIR, folder, dirs_exist_ok=True)
    try:
        jid = _queue(folder, f"Sample batch ({len(list_images(folder))} images)")
    except UserInputError as e:
        return _page(request, error=str(e))
    return RedirectResponse(f"/parcel/jobs/{jid}", status_code=303)


def _job_or_404(jid):
    j = jobs.get_job(jid)
    if not j or j["case_type"] != CASE_TYPE:
        return None
    return j


def _counts(jid):
    c = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM parcel_results WHERE job_id=? GROUP BY status", (jid,))}
    return [(s, c.get(s, 0)) for s in STATUS_ORDER if c.get(s)]


@router.get("/jobs/{jid}")
def job_page(request: Request, jid: str):
    j = _job_or_404(jid)
    if not j:
        return render(request, "error.html", status_code=404, title="Job not found", message="This parcel job does not exist.")
    rows = db.query("SELECT * FROM parcel_results WHERE job_id=? ORDER BY idx", (jid,))
    response = render(request, "parcel_job.html", active="parcel", job=j, rows=rows, counts=_counts(jid), help=STATUS_HELP,
                      events=jobs.events(jid, 8))
    jobs.remember_job(response, jid, CASE_TYPE)
    return response


@router.get("/jobs/{jid}/rows")
def rows_partial(request: Request, jid: str):
    if not _job_or_404(jid):
        return Response(status_code=404)
    rows = db.query("SELECT * FROM parcel_results WHERE job_id=? ORDER BY idx", (jid,))
    return render(request, "_parcel_rows.html", job=jobs.get_job(jid), rows=rows, counts=_counts(jid))


@router.get("/jobs/{jid}/download")
def download(jid: str):
    j = _job_or_404(jid)
    if not j or not j.get("output_path") or not Path(j["output_path"]).exists():
        return Response("Results are not ready yet.", status_code=404)
    return FileResponse(j["output_path"], filename=f"parcel_shipments_{jid}.csv", media_type="text/csv")


def _image_path(j, filename):
    p = Path(j["input_dir"]) / Path(filename).name
    return p if p.exists() and p.parent == Path(j["input_dir"]) else None


@router.get("/jobs/{jid}/image/{filename}")
def image(jid: str, filename: str):
    j = _job_or_404(jid)
    p = j and _image_path(j, filename)
    return FileResponse(p) if p else Response(status_code=404)


@router.get("/jobs/{jid}/thumb/{filename}")
def thumb(jid: str, filename: str):
    j = _job_or_404(jid)
    p = j and _image_path(j, filename)
    if not p:
        return Response(status_code=404)
    t = Path(j["input_dir"]) / ".thumbs" / (p.stem + ".jpg")
    if not t.exists():
        try:
            from PIL import Image
            t.parent.mkdir(exist_ok=True)
            with Image.open(p) as im:
                im.draft("RGB", (600, 600))
                im = im.convert("RGB")
                im.thumbnail((420, 420))
                im.save(t, "JPEG", quality=70)
        except Exception:
            return Response(status_code=404)
    return FileResponse(t, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
