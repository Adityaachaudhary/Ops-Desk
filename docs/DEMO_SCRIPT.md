# Demo + live-review cheat sheet

## 5-minute demo per case (record with any screen recorder; do not exceed 5:00)
**Case 1 (Parcels)** - 0:00 open /parcel, explain statuses panel - 0:30 drag a folder of frames (or **Run sample batch**) - 1:00 show progress, leave the page, return via *All jobs* - 1:45 filter chips: SUCCESS vs flagged; open a thumbnail; read a `notes` column - 3:00 download CSV, show columns - 3:45 upload a text file / empty file to show the clear errors - 4:30 limitations (hand/occlusion, heuristic scene check).
**Case 2 (Compliance)** - 0:00 the three inputs - 0:30 **Run sample** - 1:00 result: each verdict with its reason (Dinesh/Devendra name mismatch, Neha 86 vs 66, Sanjay missing) - 2:30 open the Excel: Summary, Employee Verification, exceptions - 3:15 name tolerance table (README) and why exact IDs - 4:00 upload the wrong PDF in a slot -> message - 4:30 limitations.
**Case 3 (P&L)** - 0:00 /pnl, delivery-channel panel - 0:30 **Run extended sample** - 1:00 dashboard tiles + heat-map - 2:00 anomalies log + insight sentence + override note - 3:00 Digests tab: preview Red and Blue digests, delivery log (dry run vs configured SMTP/Slack) - 3:45 rules/interpretations tab - 4:15 "how it gets smarter".

## Where to make a live change (60-90 min review)
| Likely request | File / line |
|---|---|
| Change a P&L target range, tolerance or colour band | `app/pnl/config.py` (`LINE_ITEMS`, `Rules`) - or `data/pnl_rules.json`, no code |
| Switch CM deviation to midpoint basis | `Rules.deviation_basis = "midpoint"` |
| Make name matching stricter / looser | `app/compliance/matcher.py`: `TYPO_MIN_LEN`, `token_match()` |
| Change amount tolerance default | upload form field, or `service.py` default `"1"` |
| Add a new parcel status or overlay template | `app/parcel/fields.py` (regex per template) / `pipeline.py` (status rules) |
| Add a new digest channel | `app/pnl/notifier.py` (`channels_configured`, `deliver`) |
| Add a cost line item | `LINE_ITEMS` + loader alias + DB column |
| Change what a status badge looks like | `app/web/static/style.css` (`.pill.*`) |
