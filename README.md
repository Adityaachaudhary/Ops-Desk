# Prozo Operations Intelligence - EIR (Agentic AI) case-study submission

One small web app, three independent automations. Each case has its **own README** with approach, assumptions, test data, limitations and improvements:

| Case | Module | README |
|---|---|---|
| 1 - Parcel images -> shipment data (OCR) | `app/parcel/` | [`app/parcel/README.md`](app/parcel/README.md) |
| 2 - Wagesheet vs PF/ESIC ECR compliance | `app/compliance/` | [`app/compliance/README.md`](app/compliance/README.md) |
| 3 - Daily FC P&L monitor & alert digest | `app/pnl/` | [`app/pnl/README.md`](app/pnl/README.md) |

For the full application design, request lifecycle, persistence model, integrations, and per-case processing flows, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Run it (about 10 minutes, free, no API keys)
```bash
git clone <repo> && cd prozo-agentic-ai
python3 -m venv .venv && . .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt                             # everything incl. Case 1 libs (RapidOCR + OpenCV; Tesseract optional fallback)
#   Cases 2 + 3 only?  pip install -r requirements-core.txt (no OpenCV; Tesseract is optional for scanned ECRs)
# OCR engines: Case 1 uses RapidOCR (bundled models via requirements) for scanner-overlay text and can fall back to Tesseract when available; physical-label OCR fallback and scanned/image-only Case 2 PDF pages need the Tesseract OS binary:
#   Ubuntu/Debian: sudo apt install tesseract-ocr    macOS: brew install tesseract    Windows: `winget install UB-Mannheim.TesseractOCR` (or the UB-Mannheim installer), then add `C:\Program Files\Tesseract-OCR` to PATH and restart the terminal
uvicorn app.main:app --port 8000                            # or ./run.sh  /  run.bat
```
Open **http://localhost:8000**. Every module has a **"Run sample"** button, so a reviewer sees results in one click, and each module accepts your own files.
Tests: `python -m pytest` (suite covering parcel field parsing/scene checks/OCR fallbacks, compliance matching and scanned-PDF OCR, P&L rules and inbox watching, a 1,500-employee multi-page ECR scale test, and API end-to-end with bad inputs).

## What the brief's "common requirements" map to
| Requirement | Where / how |
|---|---|
| Web app a non-technical user can operate | HTML/CSS pages (Jinja2, no build step): drag-and-drop upload, progress, results tables with filters, download buttons. |
| Large batch must not freeze the screen; leave and come back | Upload only saves files + creates a **job row** and returns at once. A SQLite-backed worker (`app/core/jobs.py`) processes it; the page polls progress. Jobs and results persist, are listed under *All jobs*, and **survive page reloads and server restarts** (interrupted jobs are re-queued on start). Failed jobs have a Retry button. |
| Bad input -> clear message, no crash | Validation at upload (empty, wrong type, corrupt, wrong slot) and inside jobs (`UserInputError` -> readable text). Unhandled errors render an error page, are logged, and never kill the worker thread. Each module has a "Bad input" section in its README, and `tests/test_api.py` checks it. |
| Separate or combined app | **Combined**, with hard module boundaries (below). |
| No secrets in repo; optional integrations | Credentials are read from process environment variables; `.env.example` documents example values but is not loaded automatically. Sample workflows run without external API keys. |

## Isolation between the modules
* Each module lives in its own package and provides a router, a `CASE_TYPE`, and a `handle(job, ctx)` worker function. `app/main.py` imports them one by one inside `try/except`; a module that fails to import shows as **disabled** in the sidebar and the other successfully imported modules keep working.
* **OCR remains lazy.** Case 1 reads scanner overlays primarily with RapidOCR (bundled models) and can fall back to the Tesseract OS binary when needed/available (physical-label OCR fallback currently uses Tesseract when present). Case 1 can run without the Tesseract OS binary when RapidOCR is available, but Case 2 uses Tesseract for image-only PDF pages. Case 3 does not use OCR. Text-based ECR PDFs and the other modules keep working without the Tesseract OS binary.
* The shared core (`app/core/`) provides configuration, SQLite persistence, the job queue and upload validation; case-specific processing stays in each module package. The common core is covered by API tests.

## Layout
```
app/
  main.py            app wiring, module registration, error pages, /api/jobs
  core/              config, db (SQLite), jobs, web rendering, tabular readers, OCR compatibility import
  parcel/            Case 1: overlay parsing, OCR, scene checks, batch consistency, service and routes
  compliance/        Case 2: wagesheet/ECR parsing, matching, verification, reports and routes
  pnl/               Case 3: loading, P&L rules, anomalies, insights, digest delivery, inbox watcher and routes
  web/               Jinja2 templates + static CSS/JavaScript
data/sample/         provided samples (case1 subset, case2, case3) + the extended Case 3 sample
outputs/sample_outputs/  what the system produced from those samples (CSV / XLSX / digests)
tests/               pytest suite + synthetic scale-test generator
docs/                architecture notes and a demo/live-review cheat sheet
```
Database: `data/prozo.db` (created on first run). Tables: `jobs`, `job_events`, `parcel_results`, `compliance_results`, `fc_daily_records`, `fc_anomalies`, `fc_alerts`, `pnl_inbox_seen`, `overrides`, `audit_logs`. The application architecture and table responsibilities are described in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Principles I held to
1. Never fabricate extracted data; a wrong value is worse than an honest flag.
2. Never silently mark an ambiguous employee as compliant.
3. No LLM-generated numbers. Business logic is deterministic Python; an LLM is optional and only rephrases text (Case 3).
4. Every record keeps traceability (file name / source row / reason text) so a reviewer can see *why* it was flagged.

## Honest overall limitations
See each module's README. In short: Case 1 is a heuristic OCR (RapidOCR/Tesseract) + OpenCV scene-analysis system with measured (not perfect) accuracy and human review for uncertain cases; Case 2 parses text-layer PDFs and uses local Tesseract OCR for image-only pages; Case 3 supports an opt-in local inbox watcher and digest delivery over configured SMTP and/or Slack channels. Configure and verify your own delivery credentials before relying on notifications. The app has no built-in authentication, so run it only in a trusted environment. The web UI was tested through the API test client and rendered-HTML checks, not manually in every browser.


## Windows notes
* Use Python 3.10-3.12 (tested on 3.12). Very new Python versions may lack prebuilt wheels for some libraries.
* Do not keep the project folder inside OneDrive (sync can lock the SQLite database); use e.g. `C:\prozo`.
* Case 1 overlay OCR can work via RapidOCR (installed by `requirements.txt`) when its runtime/models are available; install Tesseract separately if you need Case 1 physical-label OCR fallback or scanned/image-only Case 2 PDF OCR. Tesseract is a normal Windows program, not a pip package: install it once, then restart VS Code / the terminal so PATH refreshes (or set `TESSERACT_CMD`).
