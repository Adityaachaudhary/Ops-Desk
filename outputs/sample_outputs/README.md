# Outputs produced by the system from the sample data
| File | Produced by | Input |
|---|---|---|
| `parcel_provided_dataset_results.csv` | Parcel module, same pipeline as the web app (262 images, ~5 min on one core) | full provided image set |
| `compliance_sample.xlsx` / `.csv` | Compliance module via the web app's **Run sample** | `data/sample/case2/*` |
| `pnl_sample.xlsx`, `pnl_anomalies_sample.csv`, `digests/*.html` | P&L module via **Run extended sample** (digests are dry-run renders; no SMTP/Slack configured in my sandbox) | `data/sample/case3/case3_extended_sample.csv` |
