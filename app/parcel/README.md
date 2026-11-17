# Case Study 1 - Parcel Images -> Shipment Data

Warehouse operators upload scanner frames in bulk (hundreds at a time, JPG/PNG or a ZIP of them). For **every image** the system produces one record - AWB/tracking number, weight, dimensions (whichever are present) - and an **honest status flag** whenever a clean read is not possible. The whole batch downloads as one CSV.

## Run it (under 15 minutes, free, offline)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Tesseract OCR engine (also used for scanned Case 2 PDFs; Case 3 does not use it):
#   Ubuntu/Debian: sudo apt install tesseract-ocr     macOS: brew install tesseract
#   Windows: install the UB-Mannheim build and add it to PATH
uvicorn app.main:app --port 8000          # open http://localhost:8000/parcel
```
Click **Run sample batch** (27 frames in `data/sample/case1/`, mostly the edge cases) or drag in your own images/ZIP. No API key, no network, no GPU. If Tesseract is missing the page explains how to install it. Text-based Case 2 PDFs and Case 3 remain available without Tesseract; image-only Case 2 pages need it.
Speed: roughly **1.5-2 s per frame on one CPU core** (the full 262-frame set took ~6 minutes). On a multi-core machine set `PROZO_PARCEL_THREADS=4` (or more). Jobs run in the background; return via the Parcel case tab or *All jobs*. Use **New parcel batch** on the job page to upload another batch.

## What the data actually looks like (and why the design follows)
I opened the provided frames before designing. Every frame is a conveyor/scanner capture with a **machine-printed text block in the top-left corner** (the scanner's own record: AWB/barcode value, dimensions, weight, time), and the parcel itself photographed below it. The physical shipping label is a few dozen pixels wide in these frames - not reliably readable by any free OCR - while the overlay text is. So the pipeline reads the overlay as the primary source of AWB / weight / dimensions and uses the picture only for scene checks. There are at least **seven overlay templates** (yellow `AWB No/Length/Width/Height/Weight`, white `Barcode/Dimension:L0213XH0148XW0178/Weight:01190`, `Dimension(LxBxH): 15.5x11.1x7.7 cm`, `L * W * H: 0190 * 0369 * 1040`, green `Dim(L/W/H):0579/0428/0333mm`, `AWB_NO_ :` / `LENGTH(cm) :` ...) with different units (cm, mm, grams, kg, unit missing, zero-padded). Frames also include colour CCTV-style shots and frames with no shipment data at all.

## Pipeline (`pipeline.py`)
1. **Validate** - file opens as an image and is not tiny/corrupt, else `INVALID_IMAGE` (record kept, batch continues).
2. **OCR the overlay block** (`ocr.py`) - crop top-left, bright-text-friendly preprocessing (max-channel so yellow/green/white text all survive), up-scale/threshold, Tesseract `--psm 6`; up to four preprocessing variants are tried and the parse that yields the most fields wins (most frames finish on the first try).
3. **Parse fields** (`fields.py`, pure Python, unit-tested) - one tolerant regex family per template, unit inference (mm -> cm, g -> kg), `Barcode:41306503_19109110324251` -> long numeric part, junk after commas/symbols cut, trailing OCR noise glued to an AWB dropped **and flagged**, scanner placeholders such as `2DNOREAD` rejected, zero dimensions (`0000*0000*0000`) reported as *not available*, never as a value.
4. **Scene check** (`scene.py`, OpenCV only, no model weights) - blank the text block, find parcel-like blobs (brightness + texture, morphology, thin belt-edge bands rejected): 0 blobs -> `NO_PARCEL`; several comparable blobs -> `MULTIPLE_PARCELS`; one blob hugging the frame edge over a long span -> `PARTIAL_PARCEL`.
5. **Physical-label fallback** - if overlay fields are missing, try local OCR on the image with the detected overlay masked; decode a visible barcode when OpenCV can read it. Conflicting values are retained as review flags, not silently substituted.
6. **Validate and decide** - plausibility (weight 5 g - 100 kg, each side 0.5 - 300 cm, L*W*H vs printed volume), OCR confidence, digit-glyph ambiguity. `I/l/S/B` read as digits makes the record *risky*; `@/O/Q -> 0` (the monospace template's dotted zero) is accepted and noted.
7. **Batch cross-check** (`consistency.py`) - an AWB whose length differs from its siblings with the same 4-digit prefix, or whose prefix is one digit away from a much more common prefix, is downgraded to `LOW_CONFIDENCE` (a dropped/added/misread digit is the commonest OCR failure). It never edits a value.

## Status taxonomy
| Status | Meaning |
|---|---|
| `SUCCESS` | AWB, weight and dimensions read; nothing suspicious |
| `PARTIAL_EXTRACTION` | AWB read, weight and/or dimensions missing - **the fields that were read are kept**, `notes` says what is missing |
| `LOW_CONFIDENCE` | Fields read but OCR confidence, digit ambiguity, odd AWB length/prefix or an implausible value says *verify* |
| `LABEL_UNREADABLE` | No AWB could be read (text absent, covered, blurred, or the scanner itself printed `NOREAD`) |
| `NO_PARCEL` / `MULTIPLE_PARCELS` / `PARTIAL_PARCEL` | Scene problems. Any fields that were read are still shown but the status forces a human look (with several parcels the overlay cannot be tied to one) |
| `INVALID_IMAGE` | Corrupt/unreadable file or processing error |
`LABEL_BLOCKED` is reserved for a future reliable occlusion detector; currently such cases are flagged as `LABEL_UNREADABLE` rather than guessed. Output CSV columns: `filename, awb, weight_kg, length_cm, width_cm, height_cm, dimensions, status, needs_review, confidence, parcels_detected, notes`.

## Results on the provided dataset (262 frames, one CPU core, no tuning on a held-out set - see caveat)
Full per-image output: `outputs/sample_outputs/parcel_provided_dataset_results.csv`.

| Status | Count |
|---|---|
| `SUCCESS` | 108 |
| `PARTIAL_EXTRACTION` | 19 |
| `LOW_CONFIDENCE` | 72 |
| `LABEL_UNREADABLE` | 7 |
| `NO_PARCEL` | 30 |
| `MULTIPLE_PARCELS` | 18 |
| `PARTIAL_PARCEL` | 10 |

* AWB read on **249 / 262**, weight on **201**, full dimensions on **240**.
* **Ground truth I could verify without hand-labelling:** 157 frames have the AWB in their file name (`weight_image_<AWB>_...`, `<AWB>-<timestamp>`). Exact AWB match: **138 / 157 (88%)**. Of the 65 of these frames marked `SUCCESS`, **0 had a wrong AWB** (before the batch cross-check was added it was 5; the wrong reads are now caught as `LOW_CONFIDENCE` or flagged). The remaining misses are one digit off (e.g. `0` read as `6`) and are mostly flagged `LOW_CONFIDENCE`.
* **Scene flags are the weakest part.** I spot-checked 24 flagged frames by eye (8 per flag): `NO_PARCEL` was right for the large majority (empty belt), `MULTIPLE_PARCELS` and `PARTIAL_PARCEL` had many false alarms before I raised the thresholds; after tightening they fire on ~22% of frames. The frames named after a scene edge case behave as follows: `No Shipment packet captured` -> NO_PARCEL (correct); `Multiple shipment packets...` -> MULTIPLE_PARCELS (correct); `Half shipment captured` -> flagged (as NO_PARCEL rather than PARTIAL_PARCEL - wrong reason, right outcome: not `SUCCESS`); `No Shipment ccaptured` -> flagged; `multiple_shipment_jpeg` -> missed as multiple; `Image with worker hand on it` -> read normally (hand not detected). `LOW_CONFIDENCE` is a large bucket (about a quarter of frames) on purpose: digit ambiguity, a trailing-noise AWB or an out-of-pattern length is cheaper for a person to confirm than a wrong AWB is to find later. Caveat: the thresholds were adjusted while looking at this same dataset, so these numbers are optimistic for unseen images.

## Bad input
Nothing selected, empty file (0 bytes), unsupported extension, non-image bytes renamed `.jpg`, corrupt ZIP, ZIP with no images, > 1000 files, > 60 MB per file: clear message on the upload page, no job created. A single bad image inside a batch becomes an `INVALID_IMAGE` row; the batch completes (`completed with errors` only if a frame crashed the pipeline). Missing Tesseract -> install instructions, module disabled, rest of the app fine.

## Known limitations (honest list)
* Overlay remains the primary source because the provided labels are small and often oblique. The physical-label OCR/barcode pass is a best-effort fallback; low-resolution or rotated labels can still be unreadable. In those cases the record is flagged rather than guessed.
* **Digit errors can still pass** (`0` vs `6`, or any same-length slip) when nothing in the batch contradicts them - hence measured, not perfect, accuracy. Weight/dimension digits have no cross-check beyond plausibility and the printed volume.
* A detected-but-undecodable barcode is reported as `LABEL_UNREADABLE` with a note that the barcode may be covered or unreadable. The system does not claim `LABEL_BLOCKED` without reliable evidence distinguishing a covered label from blur or low resolution.
* **Scene flags are heuristics** with both false alarms and misses (above). They are deliberately conservative toward "needs a look".
* Units without a label are *inferred* (`01190` -> 1.19 kg; four-digit padded dimensions -> mm) and the inference is written in `notes`.
* Tesseract speed on one core is ~1.5-2 s/frame; accuracy drops on very small text (the Ekart-style frames) even with up-scaling.
* No dedupe: the same AWB in two frames yields two rows.

## With more time
Improve the physical-label crop and add dedicated QR decoding/cross-checking beyond the current OpenCV barcode fallback; a small learned classifier (or YOLO) for parcel count / partial / hand-on-label trained on the provided named edge cases; per-template OCR with whitelists (digits only) to remove glyph ambiguity; hand-labelled evaluation set + confusion matrix; review UI to correct a value and re-export; checksum/length rules per courier; GPU/parallel OCR and a Celery worker for very large batches.

## Where things live
`fields.py` (templates -> values) - `ocr.py` (Tesseract, lazy) - `scene.py` (OpenCV) - `pipeline.py` (statuses) - `consistency.py` (batch checks) - `service.py` (job handler, ZIP expansion, CSV) - `router.py` (pages, thumbnails). Tests: `tests/test_parcel_fields.py`, `tests/test_parcel_consistency.py`.
