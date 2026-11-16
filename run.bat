@echo off
if not exist .venv ( python -m venv .venv && call .venv\Scripts\activate && pip install -r requirements.txt ) else ( call .venv\Scripts\activate )
uvicorn app.main:app --port 8000
