"""Persistent background job queue (SQLite-backed, no Redis/Celery needed).

Lifecycle: queued -> processing -> completed | completed_with_errors | failed
* The HTTP request only validates + stores files + creates a job row, then returns immediately.
* Worker threads claim queued jobs atomically, so several workers / restarts are safe.
* On start-up any job left in 'processing' (server was stopped) is put back to 'queued' -> restartable.
* Handlers are plain functions registered per case type; a crash in one handler only fails that job.
"""
import json
import os
import shutil
import stat
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from . import db
from .config import OUTPUT_DIR, UPLOAD_DIR, WORKER_THREADS, MAX_UPLOAD_MB, MAX_FILES_PER_UPLOAD

HANDLERS: dict[str, Callable] = {}
_started = False


class UserInputError(ValueError):
    """Bad input that the user can fix: shown verbatim in the UI (never a stack trace)."""


def register(case_type: str, fn: Callable):
    HANDLERS[case_type] = fn


# ---------------------------------------------------------------- job CRUD
def create_job(case_type: str, title: str, input_dir: Path | None, meta: dict | None = None, total: int = 0) -> str:
    jid = "job_" + uuid.uuid4().hex[:10]
    t = db.now()
    db.execute(
        "INSERT INTO jobs(id,case_type,title,status,total_items,input_dir,meta,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (jid, case_type, title, "queued", total, str(input_dir) if input_dir else None, json.dumps(meta or {}), t, t))
    event(jid, "info", "Job created and queued")
    return jid


def get_job(jid: str) -> dict | None:
    j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    if j:
        j["meta"] = db.jloads(j["meta"], {})
        j["progress"] = int(100 * j["processed_items"] / j["total_items"]) if j["total_items"] else (
            100 if j["status"] in ("completed", "completed_with_errors") else 0)
    return j


def list_jobs(case_type: str | None = None, limit: int = 50) -> list[dict]:
    if case_type:
        rows = db.query("SELECT * FROM jobs WHERE case_type=? ORDER BY created_at DESC, rowid DESC LIMIT ?", (case_type, limit))
    else:
        rows = db.query("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
    for j in rows:
        j["meta"] = db.jloads(j["meta"], {})
        j["progress"] = int(100 * j["processed_items"] / j["total_items"]) if j["total_items"] else (
            100 if j["status"] in ("completed", "completed_with_errors") else 0)
    return rows


def update(jid: str, **fields):
    if "meta" in fields and not isinstance(fields["meta"], str):
        fields["meta"] = json.dumps(fields["meta"])
    fields["updated_at"] = db.now()
    cols = ",".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))


def event(jid: str, level: str, message: str):
    db.execute("INSERT INTO job_events(job_id,ts,level,message) VALUES(?,?,?,?)", (jid, db.now(), level, message[:2000]))


def events(jid: str, limit: int = 50) -> list[dict]:
    return db.query("SELECT ts,level,message FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT ?", (jid, limit))


def retry(jid: str) -> bool:
    j = get_job(jid)
    if not j or j["status"] not in ("failed", "completed_with_errors"):
        return False
    update(jid, status="queued", message="Re-queued by user", processed_items=0, failed_items=0, finished_at=None)
    event(jid, "info", "Retry requested")
    return True


def delete_job(jid: str):
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row or row["status"] in ("queued", "processing"):
            return False
        j = dict(row)

        output_files = [
            OUTPUT_DIR / f"parcel_{jid}.csv",
            OUTPUT_DIR / f"compliance_{jid}.xlsx",
            OUTPUT_DIR / f"compliance_{jid}.csv",
            OUTPUT_DIR / f"pnl_{jid}.xlsx",
            OUTPUT_DIR / f"pnl_anomalies_{jid}.csv",
        ]
        for path in output_files:
            if path.is_file():
                try:
                    path.unlink()
                except PermissionError:
                    path.chmod(path.stat().st_mode | stat.S_IWRITE)
                    path.unlink()
        digest_dir = OUTPUT_DIR / "digests" / jid
        if digest_dir.is_dir():
            shutil.rmtree(digest_dir, onerror=_remove_readonly)
        if j.get("input_dir") and Path(j["input_dir"]).is_dir():
            shutil.rmtree(j["input_dir"], onerror=_remove_readonly)

        for t in ("parcel_results", "compliance_results", "fc_daily_records", "fc_anomalies", "fc_alerts", "job_events"):
            c.execute(f"DELETE FROM {t} WHERE job_id=?", (jid,))
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))
    db.audit("delete_job", jid)
    return True


def _remove_readonly(func, path, exc_info):
    for target in (Path(path), Path(path).parent):
        if target.exists():
            target.chmod(target.stat().st_mode | stat.S_IWRITE)
    func(path)


# ---------------------------------------------------------------- handler context
class JobContext:
    def __init__(self, job: dict):
        self.job = job
        self.id = job["id"]
        self._last = 0.0

    def set_total(self, n: int):
        update(self.id, total_items=n)

    def progress(self, processed: int, failed: int = 0, message: str = "", force: bool = False):
        if force or time.time() - self._last > 0.4:  # throttle DB writes
            update(self.id, processed_items=processed, failed_items=failed, **({"message": message} if message else {}))
            self._last = time.time()

    def log(self, message: str, level: str = "info"):
        event(self.id, level, message)


# ---------------------------------------------------------------- worker
def _claim() -> dict | None:
    with db.conn() as c:
        row = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
        if not row:
            return None
        cur = c.execute("UPDATE jobs SET status='processing', started_at=?, updated_at=? WHERE id=? AND status='queued'",
                        (db.now(), db.now(), row["id"]))
        if cur.rowcount == 0:
            return None
    return get_job(row["id"])


def _run(job: dict):
    jid = job["id"]
    fn = HANDLERS.get(job["case_type"])
    ctx = JobContext(job)
    try:
        if fn is None:
            raise UserInputError(f"No handler registered for '{job['case_type']}' (module disabled).")
        ctx.log("Processing started")
        result = fn(job, ctx) or {}
        failed = result.get("failed", get_job(jid)["failed_items"])
        update(jid, status="completed_with_errors" if failed else "completed", finished_at=db.now(),
               processed_items=result.get("processed", get_job(jid)["total_items"]), failed_items=failed,
               message=result.get("message", "Done"), output_path=result.get("output_path"),
               **({"meta": {**job["meta"], **result["meta"]}} if result.get("meta") else {}))
        ctx.log(f"Finished: {result.get('message', 'Done')}")
    except UserInputError as e:
        update(jid, status="failed", message=str(e), finished_at=db.now())
        ctx.log(str(e), "error")
    except Exception as e:  # never let a handler bug kill the worker
        update(jid, status="failed", finished_at=db.now(),
               message="Unexpected processing error. Details were logged; you can retry the job.")
        ctx.log(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}", "error")


def _loop():
    while True:
        try:
            job = _claim()
        except Exception:
            time.sleep(1)
            continue
        if job is None:
            time.sleep(0.6)
            continue
        _run(job)


def start_workers():
    global _started
    if _started:
        return
    _started = True
    db.execute("UPDATE jobs SET status='queued', message='Resumed after restart' WHERE status='processing'")
    for i in range(WORKER_THREADS):
        threading.Thread(target=_loop, name=f"worker-{i}", daemon=True).start()


# ---------------------------------------------------------------- upload helper
def save_uploads(files, allowed_ext: set[str], what: str) -> tuple[Path, list[Path]]:
    """Validate + persist uploaded starlette UploadFile objects. Raises UserInputError with a clear message."""
    real = [f for f in files if f is not None and getattr(f, "filename", "")]
    if not real:
        raise UserInputError(f"No {what} selected. Choose at least one file and try again.")
    if len(real) > MAX_FILES_PER_UPLOAD:
        raise UserInputError(f"Too many files ({len(real)}). The limit per batch is {MAX_FILES_PER_UPLOAD}.")
    d = UPLOAD_DIR / ("up_" + uuid.uuid4().hex[:10])
    d.mkdir(parents=True)
    saved: list[Path] = []
    for f in real:
        name = Path(f.filename).name
        ext = Path(name).suffix.lower()
        if ext not in allowed_ext:
            shutil.rmtree(d, ignore_errors=True)
            raise UserInputError(f"'{name}' is not a supported file type. Allowed: {', '.join(sorted(allowed_ext))}.")
        dest = d / name
        n = 1
        while dest.exists():
            dest = d / f"{Path(name).stem}_{n}{ext}"
            n += 1
        size = 0
        with open(dest, "wb") as out:
            while chunk := f.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    out.close()
                    shutil.rmtree(d, ignore_errors=True)
                    raise UserInputError(f"'{name}' is larger than {MAX_UPLOAD_MB} MB.")
                out.write(chunk)
        if size == 0:
            shutil.rmtree(d, ignore_errors=True)
            raise UserInputError(f"'{name}' is empty (0 bytes).")
        saved.append(dest)
    return d, saved
