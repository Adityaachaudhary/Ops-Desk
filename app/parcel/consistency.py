"""Batch-level cross-checks: an AWB that does not look like its siblings is more likely an OCR slip.
Operates on finished records, pure Python (unit-testable). Only ever DOWNGRADES SUCCESS / PARTIAL_EXTRACTION to
LOW_CONFIDENCE - it never fixes or invents a value."""
from collections import Counter, defaultdict

CHECKED = ("SUCCESS", "PARTIAL_EXTRACTION")


def _hamming1(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def apply_batch_checks(rows: list[dict], min_group: int = 4) -> int:
    """rows: dicts with awb/status/notes/needs_review. Returns the number of records downgraded."""
    digit_awbs = [r for r in rows if r.get("awb") and r["awb"].isdigit() and len(r["awb"]) >= 10]
    by_prefix = defaultdict(list)
    for r in digit_awbs:
        by_prefix[r["awb"][:4]].append(r)
    prefix_count = {p: len(v) for p, v in by_prefix.items()}
    changed = 0
    for p, group in by_prefix.items():
        lens = Counter(len(r["awb"]) for r in group)
        mode, mode_n = lens.most_common(1)[0]
        for r in group:
            reasons = []
            if len(group) >= min_group and mode_n / len(group) >= 0.6 and len(r["awb"]) != mode:
                reasons.append(f"AWB has {len(r['awb'])} digits but {mode_n} of {len(group)} AWBs starting {p} have {mode} (digit dropped/added by OCR?)")
            for q, nq in prefix_count.items():
                if q != p and _hamming1(p, q) and nq >= max(5, 5 * prefix_count[p]):
                    reasons.append(f"AWB prefix {p} is rare in this batch while {q} is common ({nq}x) - one digit may be misread")
                    break
            if reasons:
                r["notes"] = "; ".join(filter(None, [r.get("notes", "")] + reasons))
                r["review_reason"] = "; ".join(filter(None, [r.get("review_reason", "")] + reasons))
                if r["status"] in CHECKED:
                    r["status"], r["needs_review"] = "LOW_CONFIDENCE", 1
                    changed += 1
    return changed