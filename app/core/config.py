"""Central configuration. Everything is overridable with environment variables; no secrets live in code."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("PROZO_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = Path(os.getenv("PROZO_OUTPUT_DIR", BASE_DIR / "outputs"))
DB_PATH = Path(os.getenv("PROZO_DB", DATA_DIR / "prozo.db"))
WEB_DIR = BASE_DIR / "app" / "web"

MAX_FILES_PER_UPLOAD = int(os.getenv("PROZO_MAX_FILES", 1000))
MAX_UPLOAD_MB = int(os.getenv("PROZO_MAX_UPLOAD_MB", 60))  # per file
WORKER_THREADS = int(os.getenv("PROZO_WORKERS", 2))
PARCEL_THREADS = int(os.getenv("PROZO_PARCEL_THREADS", 2))

for d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
