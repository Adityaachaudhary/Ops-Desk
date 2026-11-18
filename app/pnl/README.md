# Case Study 3 - Daily FC P&L Monitor & Alert Digest

Watches daily revenue and cost data per fulfilment centre (FC), tells leadership **the same day** when margins slip and **which cost line caused it**, and sends each FC manager a colour-coded digest.

## Run it (under 15 minutes, free)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-core.txt          # no OCR/OpenCV needed for this module
uvicorn app.main:app --port 8000              # open http://localhost:8000/pnl
```
Click **Run extended sample** (5 FCs x 30 days) or upload the provided `Case Study 3.xlsx` (single FC, "Day 1..100"), or your own CSV/Excel.
Tabs on the result page: **Dashboard** (latest status per FC, FC x day heat-map, CM2 trend charts) - **Anomalies log** - **Daily P&L** - **Digests** (preview any FC/day, send now, delivery log) - **Rules used**. Downloads: Excel workbook, anomalies CSV.

For unattended ingestion, set `PROZO_PNL_INBOX_DIR` to a local folder before starting the app. New `.csv`, `.tsv`, `.xlsx`, and `.xlsm` files are queued after their size and modification time remain stable for two scans. Each inbox job generates the same dashboard and workbook as a manual upload; inbox jobs do not send e-mail or Slack automatically. Manual uploads are unchanged. Use one completed daily file per FC/day when the digest should represent that day's numbers.

### Delivering digests over a real channel (free options)
The P&L page's **Analyse** and **Run extended sample** actions send the latest-day digest for each FC by default when **Send each FC's digest** is checked. Uncheck it to create the job without attempting delivery. Inbox-watcher jobs never send automatically. The Digests tab can preview and manually send any FC/day.

Configure credentials in the app process environment (the app does not load a `.env` file automatically; `.env.example` lists the supported variables):

* **E-mail (SMTP):** set `SMTP_HOST`, `SMTP_USER`, and `SMTP_PASSWORD`. For Gmail, use `smtp.gmail.com`, port `587`, and a Gmail app password (requires 2-step verification). `SMTP_PORT` defaults to `587` and uses STARTTLS; port `465` uses SSL. `SMTP_FROM` optionally sets the sender address (defaults to `SMTP_USER`), and `SMTP_TLS=0` disables STARTTLS for providers that require it. Configure each FC manager's recipient address in `data/fc_managers.json`.
* **Slack:** create an Incoming Webhook and set `SLACK_WEBHOOK_URL`, or configure a per-FC `slack_webhook` in `data/fc_managers.json`.

On Windows PowerShell, for example:
```powershell
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "you@gmail.com"
$env:SMTP_PASSWORD = "your-gmail-app-password"
$env:SMTP_FROM = "you@gmail.com"
```
Set these variables before starting or restarting the app. Do not commit credentials or real manager contact details.

If delivery is disabled or no channel is configured for a recipient, the digest is still rendered and saved to `outputs/digests/<job>/`, and its status is **dry run**. Each delivery attempt is recorded in `fc_alerts` and shown in the Digests tab as `sent`, `dry_run`, or `failed` with the failure reason. A successful `sent` status means the configured SMTP server or Slack webhook accepted the message; verify provider-side delivery before relying on it operationally.

## Input -> output
Input columns (any order/spelling variant; header row auto-detected, also inside Excel with the data not starting at A1 like the provided file): `Day` or `Date`, optional `FC`, `Revenue`, `Manpower`, `Packaging`, `Power & Fuel`, `FC Rent`, `Equipment Rentals`, `Overheads`. If the workbook has a criteria tab like the provided *Ideal FC Criteria* ("25 - 27%"), those target ranges are read from it; otherwise the defaults in `config.py` (identical values) apply.

1. **Per-day, per-FC P&L** - each cost line as % of revenue, CM1, CM1 %, CM2, CM2 % (`calculator.py`, plain Python).
2. **Anomalies log** - `Date | FC | Line Item | % of Revenue | Target Range | Status` plus severity and Rs impact.
3. **Plain-language insight** per anomalous FC-day, e.g. *"On 2026-10-30, FC Gurugram: CM2 = 7.7% (below the 12% threshold); CM1 = 59.2%. Breaches: Manpower = 32.8% (above max, target 25% - 27%); Power & Fuel = 15.2% (above max, target 10% - 12%); ... Top contributors by impact: 1) Manpower 5.8pp (~Rs 7,047), 2) Power & Fuel 3.2pp (~Rs 3,888) ... Recommend a cost audit - Manpower: review overtime, shift rosters and headcount against the day's volume; ..."* Includes deviation size, breaching lines, contributors **sorted by impact**, trailing 7-day context and a recommended action.
4. **Daily colour-coded digest per FC** to its manager (HTML e-mail / Slack text) + **central dashboard** (status tiles + FC x day heat-map).

## Rules, and the interpretations I had to make
| Brief says | Implemented as |
|---|---|
| CM1 = Revenue - Manpower - Packaging; CM2 = CM1 - remaining costs | exactly that; both as % of revenue |
| Flag a line outside its target %-of-revenue range | any value `< min` or `> max` (`ABOVE MAX` / `BELOW MIN`; below-min is noted as possible missing postings) |
| CM1% / CM2% deviating > 3pp from target | **measured from the nearest end of the target range** (CM1 target 65-70 -> flag < 62 or > 73; CM2 15-30 -> < 12 or > 33). Switchable to "distance from the midpoint" with `deviation_basis` in `config.py`/`data/pnl_rules.json`. |
| Colours | Red `< 12`; Yellow `12 <= x < 15` and `30 < x < 33`; Green `15 <= x <= 30`; Blue `x >= 33`. The brief's gaps (14.9-15, 30-30.0x, **exactly 33**) are closed deliberately; exactly 33.0 is Blue (conservative: audit). |
| Yellow days | not a rule breach by themselves, but listed as `watch` severity so the log explains every non-green tile. |
| Bad data | zero/negative revenue, missing or non-numeric cost -> **Grey / DATA ISSUE**; no margin is computed (a blank cost must not look like a 40 % margin). Duplicate FC-day rows -> warning, later row wins. |

**Alert fatigue note:** with the provided sample, every-line-in-range is rare (e.g. manpower is out of range on 75 of 100 days), so the log is large. Severity (`critical` = CM1/CM2/data, `warning` = line item, `watch` = yellow) and Rs-impact ranking keep the digest to the few items that matter; the log is filterable.

## Agentic design (honest version)
A single orchestrated workflow of four deterministic stages - **Monitor** (load, validate, compute) -> **Anomaly** (rules + trailing 7-day memory) -> **Insight** (templated explanation, optional LLM phrasing) -> **Notify** (digest + delivery + delivery log). They are separate functions/modules, not four LLMs: money maths and thresholds must be reproducible, so the LLM, when enabled, is allowed **only to rephrase** an insight and its answer is discarded unless it contains exactly the same numbers (`llm.py`). Default is fully offline/free; optional free-tier path: `GEMINI_API_KEY`.

## How this system could get smarter over time
1. **Memory of past days** - already used: each insight compares a line with its trailing 7-day mean and notes revenue dips that inflate fixed-cost %. Next: persist every day's P&L (the `fc_daily_records` table already is) and use per-FC, day-of-week baselines (Mondays vs Sundays) instead of one global range.
2. **Manual overrides** - managers can attach a note to any anomaly (`overrides` table + audit log, shown in the log and surviving re-runs). Next: let an override *suppress or re-tier* a recurring, explained breach (e.g. planned festive overtime) with an expiry date, and exclude overridden items from the digest headline.
3. **Trend tracking** - 7/30-day rolling averages, streak counters ("Power & Fuel above max 4 days running -> escalate"), CM2 slope; the dashboard already charts CM2 per FC.
4. **Closing the loop** - record the recommended action, owner and whether the line returned in range within N days; learn which actions work per line item and rank them.
5. **Per-FC thresholds** learned from history (a cold-storage FC legitimately runs higher power), with human approval, and anomaly scoring (robust z-score) layered *beside* the fixed business rules.

## Test data and outputs
`data/sample/case3/case3_extended_sample.csv` (built by `python -m app.pnl.datagen`, seeded) = 4 synthetic FCs x 30 days + the first 30 days of the provided data as a 5th FC. It guarantees, on the latest day of each FC unless noted: **clean day** (FC Gurugram 2026-10-20), **CM2 breach / Red** (FC Gurugram 2026-10-30), **line-item-only breach** (FC Bhiwandi 2026-10-08: packaging 9.4 %, CM2 stays Green; FC Kolkata 2026-10-30: power 13.1 %), **suspiciously high CM2 / Blue** (FC Bhiwandi 2026-10-30), **Yellow low** (FC Bengaluru 2026-10-30) and **Yellow high** (FC Bengaluru 2026-10-18), plus a **data-issue day** (FC Kolkata 2026-10-15, blank manpower). Outputs from this run: `outputs/sample_outputs/pnl_sample.xlsx`, `pnl_anomalies_sample.csv`, and the rendered digests in `outputs/sample_outputs/digests/`. Tests: `pytest tests/test_pnl.py` (colour boundaries incl. gaps, CM maths, clean / line-item-only / red / blue / data-issue cases).

## Bad input
Empty file, corrupt Excel, no header, missing required column (named in the message), non-numeric / negative / blank cells (row flagged, not crashed), duplicate days, no FC column (use the "FC name" box), zero revenue -> clear message or Grey/DATA ISSUE rows.

## Known limitations
* The inbox watcher is opt-in and local to one app process. It queues files, but does not manage a recurring upstream export or guarantee manager delivery. Manual uploads remain available. Automatic digest generation uses the latest day in each uploaded file; older days can be previewed/sent on demand from the Digests tab.
* SMTP/Slack delivery requires valid provider credentials and manager routing; verify delivery with your own accounts before relying on it. Failures are recorded in the Digests delivery log.
* "Memory" is limited to the data in the current upload plus overrides; no cross-run baselines yet.
* Target ranges are per line item for all FCs; per-FC ranges are not yet supported.
* Insight wording is template-based - correct and consistent, but less fluent than an LLM; recommended actions are generic per line item.
* The recommended action list is my domain assumption, not Prozo policy.

## Where things live
`loader.py` (tolerant ingest) - `config.py` (all thresholds) - `calculator.py` - `anomalies.py` - `insights.py` - `llm.py` (optional) - `notifier.py` (digest + SMTP/Slack) - `charts.py` (SVG) - `datagen.py` (sample) - `service.py` (job handler + Excel) - `router.py`.
