"""Document extraction for Case 2. PDFs are streamed page by page; image-only pages use local OCR.
Wagesheets use openpyxl/csv (no pandas)."""
import re
import os
import shutil
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

from ..core.jobs import UserInputError
from ..core.tabular import read_sheets
from .matcher import normalise


# ------------------------------------------------------------------ helpers
def clean_id(v) -> str:
    """Identifiers are strings. Keep leading zeros; repair Excel artefacts (123.0, 9.99E+11, spaces)."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip().replace(" ", "").replace("'", "")
    if s.lower() in ("nan", "none", "-", "na", "n/a", "null"):
        return ""
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if re.fullmatch(r"\d(\.\d+)?[eE]\+?\d+", s):
        try:
            s = str(int(Decimal(s)))
        except InvalidOperation:
            pass
    return s


def to_dec(v) -> Decimal | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    s = str(v).replace(",", "").replace("₹", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ wagesheet
REQUIRED = ("name",)


def _classify(h: str) -> str | None:
    h = re.sub(r"\s+", " ", str(h).strip().lower())
    if re.search(r"\buan\b", h):
        return "uan"
    if re.search(r"\bip\b", h) or re.search(r"esic\s*(no|number|ip)", h) or "insured" in h:
        return "ip"
    if "esic" in h:
        return "esic_amt"
    if re.search(r"\b(pf|epf|provident)\b", h):
        return "pf_amt"
    if re.fullmatch(r"(employee )?name( of (the )?employee)?|emp(loyee)? name|name", h) or h.startswith("name"):
        return "name"
    if "agency" in h or "vendor" in h:
        return "agency"
    if "state" in h:
        return "state"
    return None


def parse_wagesheet(path) -> list[dict]:
    sheets = read_sheets(path)
    if not any(rows for _, rows in sheets):
        raise UserInputError("The wagesheet is empty.")
    raw, header_row, cols = None, None, {}
    for _, rows in sheets:
        for i in range(min(25, len(rows))):
            m = {}
            for j, h in enumerate(rows[i]):
                k = _classify(h) if str(h).strip() else None
                if k and k not in m:
                    m[k] = j
            if "name" in m and ("uan" in m or "ip" in m):
                raw, header_row, cols = rows, i, m
                break
        if raw is not None:
            break
    if raw is None:
        raise UserInputError("Could not find the wagesheet header row. Expected columns like: "
                             "Name, UAN, IP Number, ESIC @ 0.75%, PF @ 12%.")
    if "pf_amt" not in cols and "uan" in cols:
        raise UserInputError("Wagesheet has a UAN column but no PF amount column (e.g. 'PF @ 12%').")
    if "esic_amt" not in cols and "ip" in cols:
        raise UserInputError("Wagesheet has an IP Number column but no ESIC amount column (e.g. 'ESIC @ 0.75%').")
    rows = []
    for i in range(header_row + 1, len(raw)):
        r = raw[i]
        g = lambda k: (r[cols[k]] if k in cols and cols[k] < len(r) else "")
        name = str(g("name")).strip()
        if not name and not clean_id(g("uan")) and not clean_id(g("ip")):
            continue  # blank / spacer rows
        if re.match(r"(?i)^(grand\s+)?total", name):
            continue
        rows.append({
            "row": i + 1, "name": name, "uan": clean_id(g("uan")), "ip": clean_id(g("ip")),
            "agency": str(g("agency")).strip(), "state": str(g("state")).strip(),
            "pf_amt": to_dec(g("pf_amt")), "esic_amt": to_dec(g("esic_amt")),
            "pf_raw": str(g("pf_amt")).strip(), "esic_raw": str(g("esic_amt")).strip(),
        })
    if not rows:
        raise UserInputError("The wagesheet header was found but it contains no employee rows.")
    return rows


# ------------------------------------------------------------------ PDF ECRs
@dataclass
class EcrParse:
    kind: str
    rows: list[dict] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    pages: int = 0
    header_members: int | None = None
    header_total: Decimal | None = None
    warnings: list[str] = field(default_factory=list)


NUM_TOK = re.compile(r"^-?[\d,]+(\.\d+)?$")
_ROW_CONTINUATION_STOP = re.compile(r"(?i)^(?:member details|sno\.?|sl\.\s*no\.?|page\b|total\b)")


def _header_before_first_row(text: str, row_start: re.Pattern) -> str:
    lines = []
    for line in text.splitlines():
        if row_start.match(line):
            break
        lines.append(line)
    return " ".join(lines)


def _require_ordered_header(header: str, expected: tuple[str, ...], kind: str) -> None:
    """Fail closed when a recognizable table header changes the contribution column order."""
    cursor = 0
    for label in expected:
        match = re.search(rf"\b{re.escape(label)}\b", header[cursor:], re.I)
        if not match:
            raise UserInputError(
                f"The {kind} ECR column order is not supported. Expected columns in this order: "
                f"{', '.join(expected)}. No verification report was generated."
            )
        cursor += match.end()


def _logical_member_rows(text: str, row_start: re.Pattern, is_complete) -> list[str]:
    """Join up to three continuation lines to a member row that wraps in extracted PDF text."""
    lines = text.splitlines()
    rows = []
    for index, line in enumerate(lines):
        if not row_start.match(line):
            continue
        candidate = line.strip()
        if not is_complete(candidate):
            for continuation in lines[index + 1:index + 4]:
                addition = continuation.strip()
                if not addition:
                    continue
                if row_start.match(continuation) or _ROW_CONTINUATION_STOP.match(addition):
                    break
                candidate = f"{candidate} {addition}"
                if is_complete(candidate):
                    break
        rows.append(candidate)
    return rows


def _trailing_numeric_count(text: str) -> int:
    count = 0
    for token in reversed(text.split()):
        if not NUM_TOK.match(token):
            break
        count += 1
    return count


def validate_ecr_pdf_kind(path, expected: str) -> None:
    """Reject clearly swapped text-based ECRs during upload; scanned PDFs stay on the job path."""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages[:3])
    except Exception as e:
        raise UserInputError(
            f"The {expected} ECR PDF could not be inspected ({type(e).__name__})."
        ) from e
    if not text.strip():
        return

    upper = re.sub(r"\s+", " ", text).upper()
    pf_markers = ("ELECTRONIC CHALLAN CUM RETURN", "TOTAL EPF CONTRIBUTION", "UAN", "MEMBER DETAILS")
    esic_markers = ("CONTRIBUTION HISTORY", "TOTAL IP CONTRIBUTION", "IP NUMBER", "INSURED")
    pf_score = sum(marker in upper for marker in pf_markers)
    esic_score = sum(marker in upper for marker in esic_markers)
    detected = "PF" if pf_score >= 2 and pf_score > esic_score else (
        "ESIC" if esic_score >= 2 and esic_score > pf_score else None
    )
    if detected and detected != expected.upper():
        raise UserInputError(
            f"The PDF uploaded in the {expected} slot appears to be an {detected} ECR. "
            f"Please upload the {expected} PDF in this slot."
        )


def split_pf_names(run: str) -> tuple[str, str, bool]:
    """PF ECR prints 'Name as per ECR' then 'Name as per UAN Repository' side by side.
    Return (ecr_name, repo_name, ambiguous)."""
    toks = run.split()
    n = len(toks)
    if n < 2:
        return run, run, False
    best_k, best = None, -1.0
    for k in range(1, n):
        s = fuzz.ratio(normalise(" ".join(toks[:k])), normalise(" ".join(toks[k:])))
        if s > best or (s == best and best_k is not None and abs(k - n / 2) < abs(best_k - n / 2)):
            best, best_k = s, k
    if best >= 80:
        return " ".join(toks[:best_k]), " ".join(toks[best_k:]), False
    return run, "", True


def _pages_text(path):
    import pdfplumber
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:
        raise UserInputError(f"'{getattr(path, 'name', path)}' could not be opened as a PDF "
                             f"({type(e).__name__}). It may be corrupt or password-protected.") from e
    with pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            used_ocr = False
            if not text.strip():
                try:
                    image = page.to_image(resolution=300).original
                except Exception as e:
                    raise UserInputError(f"Scanned PDF page {page_no} could not be rendered for OCR ({type(e).__name__}).") from e
                text = _ocr_page(image, page_no)
                used_ocr = True
            yield text, used_ocr
            page.flush_cache()


def _ocr_page(image, page_no: int) -> str:
    try:
        import pytesseract
    except ImportError as e:
        raise UserInputError("This PDF appears to be scanned. Install pytesseract and the Tesseract OCR engine, "
                             "or upload the original text-based ECR PDF.") from e
    exe = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
    if not exe:
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ):
            if os.path.isfile(candidate):
                exe = candidate
                break
    if not exe:
        raise UserInputError("This PDF appears to be scanned, but Tesseract OCR is not installed. "
                             "Install Tesseract or upload the original text-based ECR PDF.")
    pytesseract.pytesseract.tesseract_cmd = exe
    try:
        return pytesseract.image_to_string(image, config="--psm 6")
    except Exception as e:
        raise UserInputError(f"OCR failed on scanned PDF page {page_no} ({type(e).__name__}).") from e


def count_pages(path) -> int:
    import pdfplumber
    try:
        with pdfplumber.open(path) as p:
            return len(p.pages)
    except Exception:
        return 0


def parse_pf_ecr(path, on_page=None) -> EcrParse:
    out = EcrParse("PF")
    row_re = re.compile(r"^\s*(\d+)\s+(\d{10,14})\s+(.+)$")
    any_text = False
    for pno, (text, used_ocr) in enumerate(_pages_text(path), 1):
        out.pages = pno
        any_text |= bool(text.strip())
        if on_page:
            on_page(pno)
        if pno == 1 or out.header_members is None:
            m = re.search(r"Total Members\s+(\d+)", text)
            if m:
                out.header_members = int(m.group(1))
            m = re.search(r"Total EPF Contribution\s+([\d,]+)", text)
            if m:
                out.header_total = to_dec(m.group(1))
        header = _header_before_first_row(text, row_re)
        if re.search(r"\bEE\b", header, re.I):
            ee_header = re.search(r"\bEE\b", header, re.I)
            tail_header = header[ee_header.start():].strip()
            _require_ordered_header(tail_header, ("EE", "EPS", "ER", "Days"), "PF")
            if not re.fullmatch(r"EE\s+EPS\s+ER\s+(?:NCP\s+)?Days", tail_header, re.I):
                raise UserInputError(
                    "The PF ECR contribution columns are not in the supported rightmost order "
                    "(EE, EPS, ER, NCP Days). No verification report was generated."
                )

        def row_complete(candidate):
            match = row_re.match(candidate)
            if not match:
                return False
            tail = match.group(3).split()
            if used_ocr and tail and tail[-1].upper() in ("O", "Q"):
                return _trailing_numeric_count(" ".join(tail[:-1])) == 7
            count = _trailing_numeric_count(match.group(3))
            return count == 8 if used_ocr else count >= 5

        for line in _logical_member_rows(text, row_re, row_complete):
            m = row_re.match(line)
            if not m:
                continue
            rest = m.group(3).split()
            ocr_ambiguous = False
            if used_ocr and rest and rest[-1].upper() in ("O", "Q"):
                numeric_before = 0
                for token in reversed(rest[:-1]):
                    if NUM_TOK.match(token):
                        numeric_before += 1
                    else:
                        break
                if numeric_before == 7:
                    rest[-1] = "0"
                    ocr_ambiguous = True
            nums = []
            while rest and NUM_TOK.match(rest[-1]):
                nums.insert(0, rest.pop())
            if used_ocr and len(nums) != 8:
                out.unparsed.append(line.strip())
                continue
            if len(nums) < 5 or not rest:
                out.unparsed.append(line.strip())
                continue
            run = " ".join(rest)
            ecr_name, repo_name, amb = split_pf_names(run)
            ee = to_dec(nums[-4])  # trailing columns are: EE, EPS, ER, NCP Days
            if ee is None:
                out.unparsed.append(line.strip())
                continue
            out.rows.append({"sl": m.group(1), "id": m.group(2), "name": ecr_name, "repo_name": repo_name,
                             "amount": ee, "name_ambiguous": amb, "ocr_ambiguous": ocr_ambiguous, "page": pno})
            if ocr_ambiguous:
                out.warnings.append(f"PF UAN {m.group(2)}: a trailing OCR glyph was read as zero; verify this member manually.")
    if not any_text:
        raise UserInputError("No text could be read from the PF ECR PDF. The pages may be blank or OCR could not recognize them.")
    if not out.rows:
        raise UserInputError("No member rows (Sl.No + 12-digit UAN + contributions) were found in the file given as PF ECR. "
                             "Please check that you uploaded the EPFO ECR PDF in the PF slot.")
    _checks_pf(out)
    return out


def _checks_pf(o: EcrParse):
    if o.header_members is not None and o.header_members != len(o.rows):
        o.warnings.append(f"PF ECR header says {o.header_members} members but {len(o.rows)} rows were parsed"
                          f" ({len(o.unparsed)} unparsed lines) - results may be incomplete.")
    if o.header_total is not None:
        tot = sum((r["amount"] for r in o.rows), Decimal(0))
        if abs(tot - o.header_total) > Decimal(1):
            o.warnings.append(f"Parsed EE share total {tot} differs from header 'Total EPF Contribution' {o.header_total}.")
    if o.unparsed:
        o.warnings.append(f"{len(o.unparsed)} PF line(s) looked like member rows but could not be parsed (listed in 'Parse Warnings').")


def parse_esic_ecr(path, on_page=None) -> EcrParse:
    out = EcrParse("ESIC")
    row_re = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\d{8,17})\s+(.+?)\s+(\d{1,2})\s+(-?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)(?:\s+\S+)?\s*$")
    loose = re.compile(r"^\s*(\d+)\s+\S+\s+(\d{8,17})\s+")
    any_text = False
    for pno, (text, _) in enumerate(_pages_text(path), 1):
        out.pages = pno
        any_text |= bool(text.strip())
        if on_page:
            on_page(pno)
        header = _header_before_first_row(text, loose)
        table_header = re.search(r"\bSNo\.?\b|\bSl\.\s*No\.?\b", header, re.I)
        if table_header:
            header = header[table_header.start():]
        if table_header and re.search(r"\bIP\s+Contribution\b", header, re.I):
            markers = (
                re.search(r"\b(?:No\.?\s*Of\s*)?Days\b", header, re.I),
                re.search(r"\b(?:Total\s+)?Wages\b", header, re.I),
                re.search(r"\bIP\s+Contribution\b", header, re.I),
            )
            if any(marker is None for marker in markers) or not (
                markers[0].start() < markers[1].start() < markers[2].start()
            ):
                raise UserInputError(
                    "The ESIC ECR column order is not supported. Expected No. of Days, Total Wages, "
                    "then IP Contribution. No verification report was generated."
                )
            if not re.fullmatch(r"\s*(?:Reason)?\s*", header[markers[2].end():], re.I):
                raise UserInputError(
                    "The ESIC ECR has unsupported columns after IP Contribution. "
                    "No verification report was generated."
                )
        if out.header_total is None:
            m = re.search(r"Total IP Contribution.*?\n\s*([\d,]+\.\d+)", text, re.S)
            if m:
                out.header_total = to_dec(m.group(1))

        for line in _logical_member_rows(text, loose, lambda candidate: bool(row_re.match(candidate))):
            m = row_re.match(line)
            if m:
                out.rows.append({"sl": m.group(1), "id": m.group(3), "name": m.group(4).strip(), "repo_name": "",
                                 "amount": to_dec(m.group(7)), "wages": to_dec(m.group(6)), "page": pno})
            elif loose.match(line):
                out.unparsed.append(line.strip())
    if not any_text:
        raise UserInputError("No text could be read from the ESIC ECR PDF. The pages may be blank or OCR could not recognize them.")
    if not out.rows:
        raise UserInputError("No insured-person rows (IP Number, name, days, wages, contribution) were found in the file given "
                             "as ESIC ECR. Please check that you uploaded the ESIC contribution history PDF in the ESIC slot.")
    if out.header_total is not None:
        tot = sum((r["amount"] for r in out.rows), Decimal(0))
        if abs(tot - out.header_total) > Decimal(1):
            out.warnings.append(f"Parsed IP contribution total {tot} differs from header 'Total IP Contribution' {out.header_total}"
                                " - some rows may be missing.")
    if out.unparsed:
        out.warnings.append(f"{len(out.unparsed)} ESIC line(s) looked like member rows but could not be parsed (listed in 'Parse Warnings').")
    return out