# Case Study 1 - Parcel Images -> Shipment Data

Warehouse operators upload scanner frames in bulk (JPG/PNG or a ZIP). For **every image** the system produces one record (AWB/tracking number, weight, dimensions where present, scene/review metadata, and an **honest status flag**) and the whole batch downloads as one CSV. Images the system is not sure about are **flagged for review, never guessed**.

## Run it
```bash
python3 -m venv .venv && . .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt                             # includes rapidocr-onnxruntime, opencv-python-headless, numpy, etc.
# Optional but recommended for some cases: install the Tesseract OCR engine (OS binary)
#   Ubuntu/Debian: sudo apt install tesseract-ocr
#   macOS:         brew install tesseract
#   Windows:       winget install UB-Mannheim.TesseractOCR  (or the UB-Mannheim installer),
#                  add `C:\Program Files\Tesseract-OCR` to PATH, then restart the terminal/VS Code
# If installed in a custom location: set TESSERACT_CMD=/full/path/to/tesseract(.exe)
uvicorn app.main:app --port 8000
```
Open **http://localhost:8000/parcel**. Click **Run sample batch** (if sample images are bundled in this copy) or upload your own JPG/PNG/ZIP. Jobs run in the background; the page refreshes and the CSV is downloadable from the job screen.

**OCR engines and availability:**
- Scanner-overlay text is read primarily with **RapidOCR** (`rapidocr-onnxruntime`, models bundled in the Python package) using multiple passes/regions and merged results.
- If RapidOCR is unavailable or does not recover all three overlay fields (AWB, weight, dimensions), the pipeline tries **Tesseract** overlay OCR as a fallback when the Tesseract binary is available.
- Best-effort **physical-label OCR fallback** (`ocr.read_label`, overlay masked out) currently runs only when Tesseract is available and may recover missing fields or flag disagreements.
- `ensure_ocr()` allows Case 1 to run with RapidOCR alone or with Tesseract; the upload UI blocks and shows install guidance only if neither a working RapidOCR engine nor a Tesseract binary is available. Installing Tesseract is still recommended for physical-label OCR fallback and is required for scanned/image-only Case 2 PDF OCR.
- OCR/model loading is lazy; Case 1 is isolated from Case 2/3 at import/runtime boundaries as described in the root README.

Speed/scale notes: OCR is CPU-bound (roughly seconds per frame depending on machine/image). Images in a batch are processed in parallel (`PROZO_PARCEL_THREADS`, default 2). Jobs run in the background; results are persisted per job.

## What the data looks like (design implications)
Scanner/warehouse frames commonly include a machine-printed overlay block (AWB/barcode value, weight, dimensions, time) and the parcel below it. Overlay text is often more readable than the physical shipping label in low-resolution/oblique photos, so the pipeline treats the overlay as the primary source and uses the picture for scene checks plus best-effort label/barcode recovery when needed. Overlay layouts, units, colours, and camera styles vary (e.g. different key names/positions, cm/mm/kg/g or missing units, padded values, panel/white layouts, colour CCTV-like frames, and frames with no shipment data).

## Pipeline (`app/parcel/pipeline.py`, per image)
1. **Validate** - load with `PIL.Image.open(...).load()` and reject unreadable/corrupt/tiny images -> `INVALID_IMAGE` (row kept, batch continues).
2. **OCR the scanner overlay** (`ocr.py`) - RapidOCR-first multi-pass overlay reading: top-left block, colour-isolated text pass, native-resolution crop around detected text, and whole-image enlarged pass for alternate layouts. Pass text is merged so a field found by any pass counts. If all three fields are not recovered, Tesseract overlay OCR fallback is attempted when available (multiple preprocessing/scale/threshold variants, `--psm 6`). Returns merged text, OCR confidence, and overlay box.
3. **Parse fields** (`fields.py`, strict/template-tolerant, no guessing) - parses AWB/weight/dimensions from overlay text with normalization for common OCR/layout variants (e.g. label prefix fixes like `PROC N0.`/`AWB N0.` where safe, repeated barcode collapse only when identical, `AWB No.`/`Barcode`/`Barc0de`-style keys, weight/dimension values placed away from labels, and panel formats such as `A.W` / `L/W/H`). Units/inference/cleanup are applied where supported by the parser; ambiguity/risky OCR cases are flagged (e.g. `0/O`, `1/I/l`, `5/S`) rather than guessed. Implausible values are flagged by plausibility checks.
4. **Scene check** (`scene.py`, plain OpenCV, no model weights) - segment parcel-like components after suppressing overlay-text artifacts; uses belt/background contrast, morphology, edge/texture/busy-region filtering, and an edge-rescue pass for low-contrast/dark objects. Produces `verdict` (`OK`, `NO_PARCEL`, `MULTIPLE_PARCELS`, `PARTIAL_PARCEL`, `UNSURE`), `scene_conf`, `scene_reason`, parcel/component counts, and barcode detections/values when found.
5. **Barcode/label recovery and cross-checks** (`pipeline.py`, `ocr.py`) - if overlay AWB is missing and a single physical-label barcode decodes clearly to a valid AWB, the AWB may be recovered from the barcode. If overlay fields are missing, physical-label OCR fallback (Tesseract when available, overlay masked) may recover AWB/weight/dimensions; recovered fields are noted and overlay-vs-label disagreements are flagged for review.
6. **Fuse scene with scanner evidence** (`scene.fuse`, `pipeline.py`) - combines picture verdict with scanner-measured evidence (weight and dimensions). This avoids some hard mis-bucketing: for example, confident `NO_PARCEL` is downgraded to `UNSURE` when the scanner reported both weight and dimensions (except truncated line-scan handling), and low-confidence/unanalysable-layout scene verdicts can become `UNSURE`. `UNSURE` does not force a wrong scene bucket; the final status is decided from readable fields/quality while scene uncertainty remains visible.
7. **Status, confidence, and review reason** (`pipeline.py`) - applies status rules, plausibility/risk/OCR-confidence checks, and records `needs_review`, `confidence`, `scene_status`, `scene_confidence`, `review_reason`, and `notes`. Extracted fields are preserved even when the row needs review.
8. **Batch consistency checks** (`consistency.py`) - a separate pass over completed rows downgrades some `SUCCESS`/`PARTIAL_EXTRACTION` rows to `LOW_CONFIDENCE` when an AWB looks inconsistent with siblings (e.g. length differs from the dominant length in a sufficiently large same-prefix group, or a prefix is one digit away from a much more common prefix). It adds `review_reason`/notes and never invents or edits values.
9. **Persist & export** (`service.py`) - store rows in SQLite (`parcel_results`) and write `outputs/parcel_job_<id>.csv`.

## Status taxonomy (current implementation)
| Status | Meaning |
|---|---|
| `SUCCESS` | AWB, weight, and dimensions read; no ambiguity/plausibility/low-confidence/review-triggering issue. |
| `PARTIAL_EXTRACTION` | AWB read, but weight and/or dimensions missing. Fields that were read are kept. |
| `LOW_CONFIDENCE` | Fields read but need verification (low OCR confidence, digit/OCR ambiguity, overlay-vs-label or barcode disagreement, implausible values, or batch consistency anomaly). |
| `LABEL_UNREADABLE` | No AWB could be read from scanner overlay or physical-label OCR fallback. |
| `NO_PARCEL` | Scene analysis confidently indicates no parcel-like object in frame (subject to fusion rules with scanner evidence). |
| `MULTIPLE_PARCELS` | Scene analysis confidently indicates more than one parcel-like object; overlay data cannot be safely attributed to one parcel. |
| `PARTIAL_PARCEL` | Scene analysis confidently indicates the parcel is cut off at a frame edge. |
| `LABEL_BLOCKED` | Defined by the contract but **not emitted** by the current heuristic implementation. |
| `INVALID_IMAGE` | File could not be decoded/read or is too small. |

Notes: scene verdicts are only hard-applied as scene categories when confident/allowed by fusion; `UNSURE` scene cases remain reviewable with scene metadata and data-quality-driven status. `needs_review` is `1` for every status except `SUCCESS`.

## Output columns (CSV)
`filename, awb, weight_kg, length_cm, width_cm, height_cm, dimensions, status, needs_review, confidence, parcels_detected, scene_status, scene_confidence, review_reason, notes`

- `scene_status` / `scene_confidence`: fused scene verdict and confidence (diagnostic).
- `review_reason`: primary reason(s) a non-success row needs review (also reflected in notes for many cases).
- `confidence`: OCR-derived confidence, reduced when ambiguity/plausibility flags apply.
- Fields that were read are preserved even on non-success statuses.

## Bad input and isolation
- No files, empty files, unsupported extensions, non-image bytes renamed as images, corrupt ZIPs, ZIPs with no images, and upload/size limits are rejected or surfaced as clear errors by the core upload/job flow.
- A bad image inside a batch becomes an `INVALID_IMAGE` row; the batch can continue and complete with recorded errors as applicable.
- OCR engines are loaded lazily and Case 1 is isolated so other modules can remain available when OCR dependencies are missing or Case 1 fails to initialize.

## Results on the provided datasets (historical snapshots)
**Important:** results below are historical measurements from earlier runs and specific datasets/environments. Because OCR, scene detection, fusion, and review logic have changed, rerun batches with the current code and use the generated job CSV/UI as the source of truth for current behavior.

### Bundled parcel sample snapshot
- Historical sample output: [outputs/sample_outputs/parcel_provided_dataset_results.csv](../../outputs/sample_outputs/parcel_provided_dataset_results.csv)
- Summary recorded previously for that run (`data/parcel_samples/`, 65 images): SUCCESS 39, PARTIAL_EXTRACTION 4, LABEL_UNREADABLE 6, NO_PARCEL 7, MULTIPLE_PARCELS 4, PARTIAL_PARCEL 3, LOW_CONFIDENCE 2; AWB 43/65, weight 52/65, dimensions 42/65, review queue 26/65.

### Earlier 262-frame provided dataset snapshot (documented in previous revision)
A previous README revision reported measured results on 262 frames (one CPU core, thresholds adjusted while inspecting that dataset, so optimistic for unseen images) with full per-image output referenced as `outputs/sample_outputs/parcel_provided_dataset_results.csv` at that time. Reported status counts were: SUCCESS 108, PARTIAL_EXTRACTION 19, LOW_CONFIDENCE 72, LABEL_UNREADABLE 7, NO_PARCEL 30, MULTIPLE_PARCELS 18, PARTIAL_PARCEL 10. If that exact CSV is not present in this workspace state, treat these counts as historical documentation only and regenerate by rerunning the corresponding dataset with the current code.

## Known limitations (honest)
- **Heuristic scene detection, not a trained model.** Parcel presence/count/occlusion is inferred from OpenCV heuristics and fused with scanner-measured fields. This can still misclassify on unseen lighting, camera angles, backgrounds, motion blur, layouts, or tightly packed/occluded parcels. Low-confidence/unanalysable cases are surfaced for review, but perfect automatic bifurcation for arbitrary images is not guaranteed.
- **No learned label/box localization.** `LABEL_BLOCKED` is not emitted; label coverage/occlusion cannot be asserted reliably.
- **Layout and OCR dependence.** Multiple overlay formats are supported, but unseen scanner UI/layout changes can break parsing. OCR quality depends on image quality and available engines (RapidOCR bundled; Tesseract optional fallback/label OCR). Some text remains unreadable and is correctly flagged rather than guessed.
- **Ambiguity can remain.** Same-length digit slips or ambiguous reads may pass when evidence does not contradict them; batch checks catch some patterns but not all.
- **Single-image processing.** No cross-image parcel tracking/deduplication beyond batch-level checks such as duplicate AWB flagging.
- **UI validation scope.** Routes/services are covered by tests, but rendered UI behavior was not manually browser-tested in every environment.
- **Operations/scale.** CPU-bound OCR with in-process workers and local SQLite is suitable for moderate/demo workloads; very large backlogs or production use would need stronger queueing, observability, and deployment hardening.

## With more time
- Add a trained detector/classifier for parcel presence/count/partial/label-occlusion and evaluate on a diverse labeled dataset.
- Add layout/text-region detection and per-template parsing/OCR strategies for unseen scanner overlays.
- Strengthen physical-label localization/decoding (including QR/courier-specific checks) and cross-validate overlay vs label with calibrated confidence.
- Build a review UI for human correction/re-export and track accuracy/confusion metrics over time.
- Improve throughput/operations (queue workers, streaming progress, resumable batches, deployment-grade storage/auth).

## Where things live
```
app/parcel/
  ocr.py          RapidOCR-first overlay OCR (multi-pass/merge) + Tesseract fallback/label OCR helpers
  fields.py       template parsing, normalization, ambiguity flags, plausibility rules
  scene.py        OpenCV scene heuristics, verdict+confidence, sensor fusion
  consistency.py  batch-level AWB consistency checks (+ review reasons)
  pipeline.py     per-image orchestration, scene fusion, status/review decision
  service.py      batch job: parallel processing, persist, CSV export
  router.py       /parcel, /parcel/upload, /parcel/sample, job/CSV/download routes
  README.md       this file
data/sample/case1/            bundled sample frames (if present; used by "Run sample batch")
data/parcel_samples/          bundled sample frames in some workspace states
tests/test_parcel_pipeline.py
tests/test_parcel_consistency.py
tests/test_parcel_fields.py
tests/test_parcel_formats.py
tests/test_parcel_scene.py
```

## Sample I/O
- **Input:** one parcel photo per file (JPG/PNG), or a ZIP of images.
- **Output:** `outputs/parcel_job_<id>.csv` with one row per image and columns:
  `filename, awb, weight_kg, length_cm, width_cm, height_cm, dimensions, status, needs_review, confidence, parcels_detected, scene_status, scene_confidence, review_reason, notes`
- Example (illustrative): a clean read may be `SUCCESS` with populated fields and `needs_review=0`; an unreadable/no-AWB case may be `LABEL_UNREADABLE` with `needs_review=1`, empty AWB, and explanatory `review_reason`/`notes`. Exact values/counts depend on the image, environment, and current pipeline; use the CSV from each job as the authoritative batch record.
