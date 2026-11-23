"""SQLite persistence shared by all modules. One connection per call (thread-safe), WAL mode."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

_init_lock = threading.Lock()
_initialised = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, case_type TEXT, title TEXT, status TEXT, total_items INTEGER DEFAULT 0,
  processed_items INTEGER DEFAULT 0, failed_items INTEGER DEFAULT 0, message TEXT DEFAULT '',
  input_dir TEXT, output_path TEXT, meta TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT,
  started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS job_events(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts TEXT, level TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS parcel_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, idx INTEGER, filename TEXT, awb TEXT, weight_kg REAL,
  length_cm REAL, width_cm REAL, height_cm REAL, dimensions TEXT, status TEXT, needs_review INTEGER,
  confidence REAL, parcels_detected INTEGER, notes TEXT, scene_status TEXT, scene_confidence REAL, review_reason TEXT);
CREATE INDEX IF NOT EXISTS ix_parcel_job ON parcel_results(job_id);
CREATE TABLE IF NOT EXISTS compliance_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, idx INTEGER, employee TEXT, uan TEXT, ip_number TEXT,
  agency TEXT, state TEXT,
  pf_status TEXT, pf_reason TEXT, pf_wagesheet TEXT, pf_ecr TEXT, pf_diff TEXT, pf_ecr_name TEXT, pf_score REAL,
  esic_status TEXT, esic_reason TEXT, esic_wagesheet TEXT, esic_ecr TEXT, esic_diff TEXT, esic_ecr_name TEXT, esic_score REAL);
CREATE INDEX IF NOT EXISTS ix_comp_job ON compliance_results(job_id);
CREATE TABLE IF NOT EXISTS fc_daily_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, fc TEXT, day TEXT, sort_key REAL, revenue REAL,
  manpower REAL, packaging REAL, power_fuel REAL, fc_rent REAL, equipment REAL, overheads REAL,
  manpower_pct REAL, packaging_pct REAL, power_fuel_pct REAL, fc_rent_pct REAL, equipment_pct REAL, overheads_pct REAL,
  cm1 REAL, cm1_pct REAL, cm2 REAL, cm2_pct REAL, colour TEXT, data_issue TEXT, insight TEXT, breaches INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_fcd_job ON fc_daily_records(job_id);
CREATE TABLE IF NOT EXISTS fc_anomalies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, fc TEXT, day TEXT, line_item TEXT, pct REAL,
  target_range TEXT, status TEXT, impact_inr REAL, severity TEXT, insight TEXT);
CREATE INDEX IF NOT EXISTS ix_fca_job ON fc_anomalies(job_id);
CREATE TABLE IF NOT EXISTS fc_alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, fc TEXT, day TEXT, colour TEXT, channel TEXT,
  recipient TEXT, status TEXT, detail TEXT, subject TEXT, body_html TEXT, body_text TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_fcal_job ON fc_alerts(job_id);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, fc TEXT, day TEXT, line_item TEXT, note TEXT, actor TEXT);
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS pnl_inbox_seen(
  source_path TEXT, fingerprint TEXT, job_id TEXT, detected_at TEXT,
  PRIMARY KEY(source_path, fingerprint));
"""


def _migrate(c):
    """Add columns introduced after a database was first created (CREATE TABLE IF NOT EXISTS never alters old tables)."""
    have = {r[1] for r in c.execute("PRAGMA table_info(parcel_results)").fetchall()}
    for col, typ in (("scene_status", "TEXT"), ("scene_confidence", "REAL"), ("review_reason", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE parcel_results ADD COLUMN {col} {typ}")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    global _initialised
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    if not _initialised:
        with _init_lock:
            c.executescript(SCHEMA)
            _migrate(c)
            _initialised = True
    try:
        yield c
        c.commit()
    finally:
        c.close()


def query(sql: str, params=()) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()):
    with conn() as c:
        return c.execute(sql, params)


def insert_many(table: str, rows: list[dict]):
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
    with conn() as c:
        c.executemany(sql, [[r.get(k) for k in cols] for r in rows])


def audit(action: str, detail: str = "", actor: str = "system"):
    execute("INSERT INTO audit_logs(ts,actor,action,detail) VALUES(?,?,?,?)", (now(), actor, action, detail))


def jloads(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default