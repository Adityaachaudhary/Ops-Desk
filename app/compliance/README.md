# Case Study 2 - Wagesheet vs ECR Compliance Verification

Confirms, employee by employee, that what the vendor deducted in the wagesheet was deposited with the authorities:
**right person, right identifier, right amount** - separately for **PF** (UAN, EE share) and **ESIC** (IP Number, IP contribution).

## Run it (under 15 minutes, no paid keys; text PDFs need no Tesseract)
```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-core.txt                   # no OpenCV; scanned-PDF fallback also needs Tesseract installed on the OS
uvicorn app.main:app --port 8000                       # open http://localhost:8000/compliance
```
Click **Run sample (Aug-25)**, or upload your own three files. Text-based PDFs run without Tesseract; image-only pages use local Tesseract OCR when it is installed. Results page -> **Download Excel report** (and CSV).
The whole module also runs from the command line / tests: `pytest tests/test_compliance.py tests/test_matcher.py`.

## Input -> output
| Input | Notes |
|---|---|
| Wagesheet `.xlsx/.xls/.csv` | Header row is auto-detected (first 25 rows). Columns recognised by meaning, not position: Name, UAN, IP Number, Agency, State, `ESIC @ 0.75%`, `PF @ 12%`. |
| PF ECR PDF | EPFO PDF. Text pages are parsed directly; image-only pages are rendered and OCRed locally. Members by UAN; **EE share** = the 4th numeric column from the right (EE, EPS, ER, NCP days), so extra wage columns do not break it. |
| ESIC ECR PDF | Contribution history. Text pages are parsed directly; image-only pages are rendered and OCRed locally. IP Number, name, days, wages, **IP contribution**. |

Output workbook: `Summary` (counts per verdict for PF and ESIC, run settings, warnings) - `Employee Verification` (status + reason + both amounts + difference, PF and ESIC separately) - `PF Exceptions` - `ESIC Exceptions` - `In ECR not in Wagesheet` - `Parse Warnings`.

## Verdicts
| Verdict | Rule |
|---|---|
| **Matched** | Identifier found, name matches (tolerantly), and the amount matches exactly by default (or falls within an explicitly configured tolerance). |
| **Name mismatch** | Identifier found, ECR name is a different person. If the amount also differs this is stated in the reason. |
| **Amount mismatch** | Identifier and name fine, amount differs. Reason shows **both values and the difference** (`Submitted - Expected` = ECR - wagesheet). |
| **Missing** | UAN / IP Number not in the ECR at all (or blank in the wagesheet while an amount was deducted). |
| **Manual review** *(extra, deliberate)* | Duplicate identifier in the wagesheet or ECR; ECR row whose two name columns could not be separated; wagesheet amount unreadable; or an OCR-uncertain PF digit. These are **never auto-approved**. |
| **Not applicable** *(extra)* | No identifier **and** nothing deducted (e.g. employee outside ESIC coverage) - nothing to deposit. |

Precedence when several things are wrong: Missing -> Name mismatch -> Amount mismatch (the reason text still mentions the other problem).

## Approach and why
* **Deterministic, no LLM.** A compliance verdict must be reproducible and explainable. Everything is string/Decimal logic with tests.
* **Identifier first, exact, as strings.** UAN/IP are never fuzzy-matched and never treated as numbers (leading zeros, `1.23E+11`, `123.0` artefacts from Excel are repaired). Fuzzy logic is applied only to names, and only *after* the identifier matched, so a fuzzy score can never turn a stranger into a "match".
* **Streaming PDF parse** (`pdfplumber`, page by page, cache flushed) so hundreds of pages work; progress is reported per page. Nothing is hard-coded to the sample: rows are recognised by shape (serial + 12-digit UAN / 8-17-digit IP + trailing numbers).
* **Self-checks on the parse:** parsed member count vs the ECR header's *Total Members*, parsed EE total vs *Total EPF Contribution*, parsed IP total vs *Total IP Contribution*. A mismatch produces a visible warning, and lines that look like member rows but could not be parsed are listed - so a silently dropped employee cannot hide as "Missing".
* **Money:** `Decimal`, no floats. Default tolerance is **Rs 0 (exact)**, as specified by the brief. An operator can enter a non-zero tolerance only when policy explicitly permits rounding; the chosen tolerance and amount difference remain visible in the report.

## Name matching tolerance (the heart of the problem)
Normalise: unidecode -> uppercase -> strip punctuation/digits/honorifics (MR, MRS, SHRI, SMT, DR...) -> collapse spaces. Then:

| Case | Result |
|---|---|
| identical; same words in another order (`KUMAR RAHUL`); spacing (`SUNITA DEVI` / `SUNITADEVI`) | match |
| initial vs full word (`R KUMAR` / `RAHUL KUMAR`) | match |
| spelling variants: doubled letters, `PH/F`, `W/V`, `Y/I`, vowel changes (`MOHAMMAD`/`MOHAMMED`, `SHARMAA`) | match (note shown) |
| one-letter typo in a **long** word (>= 8 letters) | match (note shown) |
| one extra / missing word (`ANIL KUMAR` / `ANIL KUMAR SINGH`), only if the shorter name has >= 2 words | match (note: "one word missing") |
| different first name (`DINESH`/`DEVENDRA`, `REKHA`/`RENU`), one-letter difference in a short word (`RAMESH`/`RAKESH`), different surname, 2+ words missing | **mismatch** |

The bias is intentional: the cost of waving a wrong person through is higher than the cost of a reviewer glancing at a variant. Accepted variants are still annotated in the reason column (`name accepted as spelling variant`), so they are auditable. Thresholds are constants at the top of `matcher.py`; 15 parametrised cases are in `tests/test_matcher.py`.

## Results on the provided sample (12 employees)
| | Matched | Name mismatch | Amount mismatch | Missing |
|---|---|---|---|---|
| PF | 9 | 1 (Dinesh vs Devendra Prasad) | 1 (Vikram Thakur) | 1 (Sanjay Paswan) |
| ESIC | 8 | 1 (Rekha vs Renu Sharma) | 1 (Neha Mishra: 86 vs 66, difference -20) | 2 (Sanjay Paswan, Pooja Gupta) |

Outputs: `outputs/sample_outputs/compliance_sample.xlsx`. A **synthetic 300+-page stress set** (generated by `python -m tests.make_big_ecr`) checks scale and name-noise handling; see "Scale test" below.

## Scale test (synthetic, with ground truth)
`python -m tests.make_big_ecr` writes a wagesheet plus PF and ESIC ECR PDFs of **13,500 employees / 276 pages each** in the sample's text layout, with injected faults (missing IDs, amount errors, wrong names) and 8 % benign name noise (reversed word order, double spaces, case). On one CPU core: each PDF parses in **~35-40 s**, matching 13,500 employees takes **0.4 s**, **0 unparsed lines, 0 checksum warnings**. Scored against the injected truth (27,000 PF+ESIC verdicts): every Missing (271 PF / 290 ESIC) and Amount mismatch (271 / 280) found, 247 / 267 wrong names caught, **zero false alarms on the benign variations**. The 11 "misses" are all the generator replacing the *last word* of a 3-word name, which leaves a legitimate "one word missing" variant (e.g. `ANIL THAKUR KHAN` vs `ANIL KHAN`) - accepted by design; flip `ALLOW_ONE_MISSING_WORD = False` in `matcher.py` to be stricter. This proves scale and the matching policy, **not** that real EPFO/ESIC layouts all parse - see limitations. `tests/test_scale.py` runs a 1,500-employee version.

## Bad input
Missing file slot, wrong extension, empty file, renamed/corrupt PDF, password-protected PDF, scanned (no text layer) PDF, text-based PF/ESIC PDFs uploaded in the opposite slots, or a wagesheet without required headers -> a plain-language message on the upload page or on the failed job. The upload preflight checks the first three text pages for clear PF/ESIC markers; image-only PDFs and unrecognized layouts continue through the existing background job validation.

## Known limitations (honest list)
* **OCR is a fallback, not a guarantee.** Scanned/image-only pages require the Tesseract OS binary and can still produce unparsed rows; parse warnings and total checks remain important. PF OCR rows with an uncertain final digit are sent to Manual review instead of being auto-approved. Text-layer PDFs continue to use the original parser.
* Only the sample ECR layouts were available. Wrapped member rows are joined across up to three extracted text lines. Recognized PF/ESIC table headers are checked for the supported contribution-column order; a detected reordered header fails closed with a clear message instead of risking the wrong amount. Other unseen headers may still require adaptation; parse warnings and total checks remain important.
* PF "Name as per ECR" and "Name as per UAN Repository" print side by side with only a space between them; they are separated by finding the split where both halves are most similar. If that is ambiguous the row goes to **Manual review**.
* A person appearing twice in the wagesheet with *different* UANs is not detected as a duplicate person.
* No check that the wagesheet's own 12 % / 0.75 % arithmetic is correct against wages (only wagesheet vs ECR, as specified).
* Verified on the 12-row sample plus synthetic data; no real 100s-of-pages ECR was available to test against.

## With more time
PDF-coordinate column detection and support for additional verified ECR layouts; improve OCR robustness and add side-by-side PDF page preview for reviewers; check ECR wages x 12 % / 0.75 % independently; person-level duplicate detection (same name + DOB/Aadhaar-last-4); month-over-month tracking of recurring mismatches.

## Where things live
`parser.py` (Excel + PDF extraction, checksums) - `matcher.py` (names) - `verifier.py` (verdict logic) - `report.py` (Excel) - `service.py` (job handler) - `router.py` (pages/API).
