import csv
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import db
from ..core.config import OUTPUT_DIR, PARCEL_THREADS
from ..core.jobs import UserInputError
from .consistency import apply_batch_checks

IMG_EXT = {".jpg", ".jpeg", ".png"}
CSV_COLS = ["filename", "awb", "weight_kg", "length_cm", "width_cm", "height_cm", "dimensions", "status", "needs_review",
            "confidence", "parcels_detected", "notes"]


def expand_zips(folder: Path, max_files: int = 2000):
    """Unzip any uploaded .zip next to the images (zip-slip safe, images only)."""
    for z in list(folder.glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                n = 0
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if info.is_dir() or Path(name).suffix.lower() not in IMG_EXT or name.startswith(("._", ".")) or "__MACOSX" in info.filename:
                        continue
                    n += 1
                    if n > max_files:
                        raise UserInputError(f"'{z.name}' contains more than {max_files} images.")
                    dest = folder / name
                    k = 1
                    while dest.exists():
                        dest = folder / f"{Path(name).stem}_{k}{Path(name).suffix}"
                        k += 1
                    with zf.open(info) as src, open(dest, "wb") as out:
                        out.write(src.read())
        except zipfile.BadZipFile as e:
            raise UserInputError(f"'{z.name}' is not a valid zip file.") from e
        z.unlink()


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMG_EXT)


def handle(job, ctx):
    from . import ocr
    from .pipeline import process_image
    try:
        ocr.ensure_ocr()
    except ocr.OcrUnavailable as e:
        raise UserInputError(str(e)) from e
    jid = job["id"]
    files = list_images(Path(job["input_dir"]))
    if not files:
        raise UserInputError("No JPG/PNG images were found in the upload.")
    db.execute("DELETE FROM parcel_results WHERE job_id=?", (jid,))
    ctx.set_total(len(files))
    buf, done, failed = [], 0, 0

    def work(item):
        i, f = item
        try:
            r = process_image(f)
            r["_ok"] = True
        except Exception as e:  # one bad image must not sink the batch
            r = {"filename": f.name, "awb": "", "weight_kg": None, "length_cm": None, "width_cm": None, "height_cm": None, "dimensions": "",
                 "status": "INVALID_IMAGE", "needs_review": 1, "confidence": 0.0, "parcels_detected": None,
                 "notes": f"Processing error: {type(e).__name__}: {e}"[:300], "_ok": False}
        r["idx"] = i
        return r

    with ThreadPoolExecutor(max(1, PARCEL_THREADS)) as ex:
        for r in ex.map(work, enumerate(files, 1)):
            done += 1
            failed += 0 if r.pop("_ok") else 1
            buf.append({**r, "job_id": jid})
            if len(buf) >= 10:
                db.insert_many("parcel_results", buf)
                buf = []
            ctx.progress(done, failed, message=f"Processed {done}/{len(files)}")
    db.insert_many("parcel_results", buf)
    rows = db.query("SELECT * FROM parcel_results WHERE job_id=? ORDER BY idx", (jid,))
    before = {r["id"]: (r["status"], r["notes"]) for r in rows}
    apply_batch_checks(rows)  # cross-record sanity: odd AWB length / rare prefix -> LOW_CONFIDENCE
    for r in rows:
        if (r["status"], r["notes"]) != before[r["id"]]:
            db.execute("UPDATE parcel_results SET status=?, needs_review=?, notes=? WHERE id=?", (r["status"], r["needs_review"], r["notes"], r["id"]))
    out = OUTPUT_DIR / f"parcel_{jid}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    ok = sum(1 for r in rows if r["status"] == "SUCCESS")
    return {"processed": len(files), "failed": failed, "output_path": str(out),
            "message": f"{len(files)} images: {ok} clean reads, {len(files) - ok} flagged for review"}
