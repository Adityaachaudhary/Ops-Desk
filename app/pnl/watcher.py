"""Optional, local P&L inbox watcher. Disabled unless PROZO_PNL_INBOX_DIR is configured."""
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from ..core import db, jobs
from ..core.config import UPLOAD_DIR
from ..core.jobs import UserInputError
from .loader import load_daily

log = logging.getLogger("prozo.pnl.inbox")
SUPPORTED = {".csv", ".tsv", ".xlsx", ".xlsm"}


class InboxWatcher:
    def __init__(self, inbox: Path, default_fc: str = "FC-1"):
        self.inbox = inbox
        self.default_fc = default_fc
        self.observed: dict[str, tuple[int, int, int]] = {}

    def scan_once(self):
        self.inbox.mkdir(parents=True, exist_ok=True)
        next_observed = {}
        for path in sorted(self.inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            try:
                stat = path.stat()
                key = str(path.resolve()).casefold()
            except OSError:
                log.exception("Could not inspect inbox file %s", path)
                continue
            previous = self.observed.get(key)
            stable = previous[2] + 1 if previous and previous[:2] == (stat.st_size, stat.st_mtime_ns) else 1
            next_observed[key] = (stat.st_size, stat.st_mtime_ns, stable)
            if stable < 2:
                continue
            try:
                self._queue_if_new(path, key, self._fingerprint(path))
            except OSError:
                log.exception("Could not process inbox file %s", path)
        self.observed = next_observed

    @staticmethod
    def _fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _queue_if_new(self, source: Path, key: str, fingerprint: str):
        if db.one("SELECT job_id FROM pnl_inbox_seen WHERE source_path=? AND fingerprint=?", (key, fingerprint)):
            return
        title = f"Inbox: {source.name}"
        try:
            rows, _ = load_daily(source, self.default_fc)
        except UserInputError as e:
            jid = jobs.create_job("pnl", title, None, {"file": str(source), "default_fc": self.default_fc, "send": False})
            jobs.update(jid, status="failed", message=str(e), finished_at=db.now())
            jobs.event(jid, "error", str(e))
            self._mark_seen(key, fingerprint, jid)
            return

        if self._fingerprint(source) != fingerprint:
            return
        folder = UPLOAD_DIR / ("up_inbox_" + uuid.uuid4().hex[:10])
        folder.mkdir(parents=True)
        try:
            saved = folder / source.name
            shutil.copy2(source, saved)
            jid = jobs.create_job(
                "pnl", title, folder,
                {"file": str(saved), "default_fc": self.default_fc, "send": False},
                total=len({row["fc"] for row in rows}) + 2,
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._mark_seen(key, fingerprint, jid)
        jobs.event(jid, "info", f"Queued from local inbox file {source.name}; automatic delivery is disabled for inbox jobs.")
        log.info("Queued P&L inbox file %s as %s", source.name, jid)

    @staticmethod
    def _mark_seen(key: str, fingerprint: str, jid: str):
        db.execute(
            "INSERT OR IGNORE INTO pnl_inbox_seen(source_path,fingerprint,job_id,detected_at) VALUES(?,?,?,?)",
            (key, fingerprint, jid, db.now()),
        )


_thread = None
_start_lock = threading.Lock()


def _watch(watcher: InboxWatcher, interval: int):
    while True:
        try:
            watcher.scan_once()
        except Exception:
            log.exception("P&L inbox scan failed")
        time.sleep(interval)


def start_watcher():
    """Start the watcher only when the operator explicitly configures an inbox path."""
    global _thread
    raw = os.getenv("PROZO_PNL_INBOX_DIR", "").strip()
    if not raw:
        log.info("P&L inbox watcher disabled; PROZO_PNL_INBOX_DIR is not set")
        return False
    with _start_lock:
        if _thread and _thread.is_alive():
            return True
        interval = int(os.getenv("PROZO_PNL_INBOX_INTERVAL", "10"))
        if interval < 2:
            raise ValueError("PROZO_PNL_INBOX_INTERVAL must be at least 2 seconds")
        inbox = Path(raw).expanduser().resolve()
        watcher = InboxWatcher(inbox, os.getenv("PROZO_PNL_DEFAULT_FC", "FC-1").strip() or "FC-1")
        _thread = threading.Thread(target=_watch, args=(watcher, interval), name="pnl-inbox-watcher", daemon=True)
        _thread.start()
    log.info("P&L inbox watcher started for %s (poll interval: %ss)", inbox, interval)
    return True
