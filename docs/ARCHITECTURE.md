# Application architecture

This document describes the application as implemented in the repository. It covers the shared web/job platform and the processing flow for each of the three cases.

## 1. System overview

The application is a single-process FastAPI service with three separately registered case modules. The browser UI is rendered with Jinja2 templates and plain JavaScript. Long-running case processing is dispatched to background worker threads and its state is persisted in SQLite; upload handlers still perform file checks and fail-fast validation before queuing.

```text
Browser
  |  HTML forms, result pages, progress polling, digest preview
  v
FastAPI (app/main.py)
  |-- shared pages and job APIs
  |-- /parcel      -> Case 1 router -> parcel handler
  |-- /compliance  -> Case 2 router -> compliance handler
  `-- /pnl         -> Case 3 router -> P&L handler
                              ^
                              | queued jobs
SQLite (jobs, events, results) <--> core.jobs worker threads
                              |
                              +--> uploaded inputs in data/uploads/
                              `--> reports and digests in outputs/

Optional: Case 3 inbox watcher -> queues P&L jobs from a local folder
Optional: Case 3 notifier -> SMTP e-mail and/or Slack webhook
Optional: Case 3 insight rephrasing -> Gemini API
```

## 2. Application startup and module boundaries

`app/main.py` creates the FastAPI application, mounts `app/web/static/`, and imports the parcel, compliance, and P&L routers independently. Each router provides a `CASE_TYPE` and a `handle(job, ctx)` function, which is registered with `core.jobs`. If a module import fails, that module is marked unavailable in the navigation and the other successfully imported modules remain available.

At startup, the app initializes the SQLite schema and starts the configured worker threads. It also attempts to start the P&L inbox watcher; the watcher remains idle unless `PROZO_PNL_INBOX_DIR` is configured.

The shared core is intentionally small:

* `core.config` defines paths and environment-based operational settings.
* `core.db` owns the SQLite schema and thread-safe database helpers.
* `core.jobs` handles upload storage, job state, progress, retries, and worker execution.
* `core.web` provides the Jinja2 template renderer and shared module/status metadata.
* `core.tabular` reads CSV/TSV and Excel workbooks for Cases 2 and 3.
* `core.ocr_compat` imports `pytesseract` without requiring an otherwise unused pandas import.

## 3. Shared request and job lifecycle

1. A user submits an upload or sample form to a case router. The router validates required files and supported extensions; the shared upload helper enforces the per-file size and upload-count limits and stores files under the configured data directory.
2. Case-specific fast validation runs before queuing where practical. The router creates a `jobs` row with the case type, input path(s), options, and initial total, then redirects to that job's page. The HTTP request does not wait for the full analysis.
3. Worker threads repeatedly claim queued jobs from SQLite. The handler receives the job metadata and a `JobContext` that updates progress and writes events. Long jobs can therefore report page, image, or fulfilment-centre progress.
4. The handler writes structured results to case-specific tables and generated files to the output directory. The job is marked `completed`, `completed_with_errors`, or `failed`.
5. The browser polls `/api/jobs/{id}` while a job is queued or processing. Parcel results can also refresh through the parcel rows endpoint. When work finishes, the page reloads to show the final result and download links.

The job state flow is:

```text
queued -> processing -> completed
                    -> completed_with_errors
                    -> failed
```

On startup, jobs left in `processing` are returned to `queued`. Users can retry failed or completed-with-errors jobs; each handler clears its own job-scoped result rows before recomputing. A retry of a P&L job can repeat an external notification if the earlier attempt sent it before a later processing failure, so delivery is not an exactly-once guarantee. Completed jobs can be deleted; deletion removes that job's inputs, generated outputs, events, and result rows.

`UserInputError` is used for actionable input problems and its message is shown to the user. Unexpected handler exceptions fail only the current job, are recorded in its event log, and produce a generic failure message with retry available. Unexpected web-request exceptions are logged and rendered as an error page or JSON error.

Shared routes include the home page, All jobs, `/api/health`, `/api/jobs/{id}`, `/api/jobs/{id}/events`, and job retry/delete actions. Case routes provide upload/sample, result, and download endpoints; Case 3 also provides digest preview/send and manager-note actions.

## 4. Case 1 - Parcel images to shipment data

**Code:** `app/parcel/`

**Input:** JPG/JPEG/PNG files or a ZIP containing images. A job produces one result row per image and a CSV report.

**Processing flow:**

1. The router checks that Tesseract is usable, saves the upload, safely extracts supported images from ZIP files, and queues the batch.
2. The service processes images concurrently using `PROZO_PARCEL_THREADS`. An unreadable or failed image becomes an `INVALID_IMAGE` result instead of stopping other images in the batch.
3. `pipeline.process_image` opens and validates the image, then calls the OCR module to read the scanner overlay. `fields.py` interprets supported overlay formats and normalizes AWB, weight, and dimensions.
4. If overlay values are missing, OCR tries the physical label; OpenCV's barcode detector is also used as a fallback where possible. Different values are retained as review warnings rather than silently reconciled.
5. `scene.py` uses OpenCV image heuristics (no trained model) to estimate parcel count and whether a parcel is cut off. Plausibility checks, OCR confidence, digit ambiguity, and batch-level AWB consistency determine whether a result needs review.
6. Results are stored in `parcel_results` and written to `outputs/parcel_<job>.csv`. Statuses include `SUCCESS`, `PARTIAL_EXTRACTION`, `LOW_CONFIDENCE`, `LABEL_UNREADABLE`, scene flags, and `INVALID_IMAGE`. `LABEL_BLOCKED` is reserved but is not currently emitted by the pipeline.

Tesseract is a separate OS executable as well as a Python package dependency. OCR is imported lazily so other case modules do not need it. Case 1's result is a heuristic extraction, not a guarantee of correct values; ambiguous values are flagged for human review.

## 5. Case 2 - Wagesheet and PF/ESIC ECR reconciliation

**Code:** `app/compliance/`

**Input:** One wagesheet (`.xlsx`, `.xlsm`, or `.csv`) plus one PF ECR PDF and one ESIC ECR PDF. Legacy `.xls` spreadsheets are not supported. The router validates the three required slots and checks the PDFs and wagesheet before queuing.

**Processing flow:**

1. `parser.py` locates a wagesheet header by column meaning rather than fixed column positions, then normalizes employee identifiers and amounts.
2. Each PDF is streamed page by page with `pdfplumber`. Text-layer pages are parsed directly. A page without extractable text is rendered and passed to local Tesseract OCR.
3. The PF/ESIC parsers extract member identifiers, names, and contribution amounts, and compare parsed row counts/totals with PDF header totals where available. Unparsed member-like lines and discrepancies become warnings.
4. `verifier.py` matches UAN and IP identifiers exactly as strings. Names are compared tolerantly only after an identifier match; contribution amounts use `Decimal` and the selected rupee tolerance. Duplicate or ambiguous rows are sent to `Manual review`, not auto-approved.
5. Results are stored in `compliance_results`. The service writes an Excel workbook with summary, employee verification, exceptions, and parse warnings, plus a CSV export.

The default amount tolerance is Rs 1. Text-based PDFs do not require Tesseract; scanned/image-only pages do. The reconciliation is deterministic and does not use an LLM.

## 6. Case 3 - Fulfilment-centre P&L monitoring and digests

**Code:** `app/pnl/`

**Input:** CSV/TSV or `.xlsx`/`.xlsm` data with daily revenue and cost lines, optionally separated by fulfilment centre (FC). Legacy `.xls` spreadsheets are not supported. Workbook criteria can supply target ranges; otherwise the defaults in `pnl/config.py` apply. Optional JSON files under the data directory hold rule overrides and FC manager routing.

**Processing flow:**

1. `loader.py` detects headers and normalizes rows. Invalid/missing values are retained as data issues rather than used to calculate misleading margins.
2. `calculator.py` computes line-item percentages, CM1, CM2, and colour status deterministically. `anomalies.py` checks cost and margin thresholds, assigns severity/impact, and adds recent same-FC trend context.
3. `insights.py` produces a templated explanation and recommended action. If `GEMINI_API_KEY` is set, `llm.py` may rephrase the text; the response is used only if its numbers exactly match the deterministic text. The model never calculates financial values.
4. The service builds a latest-day digest for each FC, renders and saves it, and attempts configured delivery when the job's send option is enabled. SMTP e-mail requires both `SMTP_HOST` and an email address for that FC; Slack uses a global webhook or an FC-specific webhook. Every attempt is recorded in `fc_alerts`.
5. Daily records, anomalies, and delivery attempts are stored in `fc_daily_records`, `fc_anomalies`, and `fc_alerts`. The service writes an Excel workbook and an anomalies CSV. The UI provides dashboard, anomaly, daily P&L, digest, and rules views.

Manual upload and sample forms enable sending by default; the user can uncheck the send option. The Digests tab can preview and manually send a selected FC/day. With no applicable delivery channel, the digest is saved as HTML and marked `dry_run`. Provider acceptance is recorded as `sent`; the app cannot guarantee final delivery to a mailbox or Slack destination.

### Optional local P&L inbox watcher

Set `PROZO_PNL_INBOX_DIR` to enable the watcher. It polls for stable `.csv`, `.tsv`, `.xlsx`, and `.xlsm` files, fingerprints their content, and queues a staged copy only when that path/content pair has not been seen before. `PROZO_PNL_INBOX_INTERVAL` controls polling (default 10 seconds; minimum 2), and `PROZO_PNL_DEFAULT_FC` supplies a fallback FC name. Inbox jobs explicitly disable automatic e-mail and Slack delivery; their digests can still be reviewed and sent manually later.

## 7. Persistence and generated files

SQLite is the shared source of truth for job state and structured results. `core.db` uses WAL mode, a connection per operation, and a 30-second busy timeout.

| Table | Purpose |
|---|---|
| `jobs`, `job_events` | Job metadata, state, progress, and event history |
| `parcel_results` | Per-image extracted fields and status |
| `compliance_results` | Per-employee PF and ESIC verdicts |
| `fc_daily_records`, `fc_anomalies`, `fc_alerts` | P&L calculations, detected issues, and delivery log |
| `pnl_inbox_seen` | Inbox path/content fingerprints used to avoid repeat queuing |
| `overrides`, `audit_logs` | Manager notes and audit events |

Defaults are `data/prozo.db`, `data/uploads/`, and `outputs/`. `PROZO_DB`, `PROZO_DATA_DIR`, and `PROZO_OUTPUT_DIR` can relocate them. Job deletion removes its associated uploads, generated report files, digests, and job-scoped database rows. Manager/rule JSON configuration files under the data directory are not job outputs.

## 8. Configuration and external dependencies

Core operational settings are read from process environment variables:

| Variable | Effect |
|---|---|
| `PROZO_DATA_DIR`, `PROZO_OUTPUT_DIR`, `PROZO_DB` | Data, output, and SQLite locations |
| `PROZO_MAX_FILES`, `PROZO_MAX_UPLOAD_MB` | Upload-count and per-file size limits |
| `PROZO_WORKERS`, `PROZO_PARCEL_THREADS` | Background worker count and Case 1 per-job image concurrency |
| `PROZO_PNL_INBOX_DIR`, `PROZO_PNL_INBOX_INTERVAL`, `PROZO_PNL_DEFAULT_FC` | Optional Case 3 file watcher |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS` | Optional SMTP delivery |
| `SLACK_WEBHOOK_URL` | Optional global Slack webhook |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional Case 3 insight rephrasing |
| `TESSERACT_CMD` | Optional explicit path to `tesseract.exe`/Tesseract executable |

`.env.example` documents example settings, but the application does not load a `.env` file itself; the variables must be present in the process environment. Never commit real credentials or manager contact details.

`requirements-core.txt` supports Cases 2 and 3 without OpenCV; `requirements.txt` includes the additional Case 1 packages. Tesseract itself must be installed separately on the operating system for Case 1 or scanned-PDF OCR in Case 2. SMTP, Slack, and Gemini require network access only when those optional integrations are enabled.

## 9. UI, API, and security boundaries

The UI uses server-rendered Jinja2 templates, one shared stylesheet, and plain JavaScript for file drag/drop, progress polling, result filtering, digest preview, and job deletion. There is no JavaScript build step.

The app has no user authentication or role-based authorization in the current implementation. Run it only on a trusted local machine or behind an appropriately secured private network/reverse proxy; do not expose the development server directly to the public internet. Uploaded source files and outputs are stored locally unless an explicitly configured external integration is used.

## 10. Tests

The pytest suite covers API pages and invalid inputs, Case 1 parsing/pipeline behavior, Case 2 matching and OCR fallback, Case 3 calculations and inbox watching, and a generated ECR scale case. Run all tests with:

```bash
python -m pytest
```

More detailed business assumptions and known accuracy limitations are documented in the README for each case.
