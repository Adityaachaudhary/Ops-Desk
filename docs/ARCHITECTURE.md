# Architecture notes

```
Browser (HTML/CSS + ~80 lines JS)
   |  multipart upload / polling GET /api/jobs/{id}
FastAPI app (app/main.py) ---- error pages, /api/health, job retry/delete
   |-- parcel router ------\
   |-- compliance router ---+--> core.jobs.create_job()  (returns immediately)
   |-- pnl router ----------/
SQLite (WAL)  <---  worker threads (core/jobs.py)  claim 'queued' -> handler(job, ctx) -> results tables
                       handlers: parcel.service.handle | compliance.service.handle | pnl.service.handle
```
* **Job lifecycle:** `queued -> processing -> completed | completed_with_errors | failed`. Progress counters (`processed/total/failed`) are written by the handler through `JobContext.progress()` (throttled). Start-up re-queues anything left in `processing` (restartable). Handlers delete their previous results first, so retry is idempotent.
* **Errors:** `UserInputError` = fixable by the user, message shown verbatim. Any other exception is logged to `job_events` and shown as a generic message with a Retry button; the worker loop never dies.
* **Why SQLite + threads, not Celery + Redis:** zero extra services for a reviewer to install; WAL mode gives safe concurrent reads while a worker writes. The `JobContext`/handler interface is the seam where Celery/RQ would plug in.
* **Why server-rendered HTML:** no Node toolchain, nothing to build, one stylesheet; the page degrades to plain forms.
* **Data flow per case** is described in the module READMEs.
