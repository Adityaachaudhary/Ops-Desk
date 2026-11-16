#!/usr/bin/env sh
# One-command start (Linux/macOS). Creates a venv on first run.
set -e
[ -d .venv ] || { python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt; }
. .venv/bin/activate
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
