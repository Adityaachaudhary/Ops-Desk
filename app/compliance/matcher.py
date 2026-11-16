"""Name normalisation + tolerant comparison. Deliberately conservative: a different person must never
be waved through, but spelling noise and word-order differences must not raise false alarms.

Rules (all documented in app/compliance/README.md):
 1. Normalise: unidecode -> upper -> drop punctuation/digits/honorifics -> collapse spaces.
 2. Equal / equal after sorting words / equal after removing spaces         -> match.
 3. Otherwise every word of the shorter name must be matched to a distinct word of the longer one where
    a word pair matches if: equal | initial vs full word (R vs RAHUL) | same word after collapsing doubled
    letters and Y/I, W/V, PH/F variants (MOHAMMAD/MOHAMMED, KUMARR) | one-letter typo when both words are long (>=8 letters).
 4. The longer name may carry ONE extra word (missing middle/surname) only if the shorter has >=2 words.
 Anything else (e.g. DEVENDRA vs DINESH, REKHA vs RENU, RAMESH vs RAKESH) -> mismatch.
"""
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from unidecode import unidecode

HONORIFICS = {"MR", "MRS", "MS", "MISS", "SHRI", "SMT", "SHRIMATI", "SRI", "DR", "LATE"}
TYPO_MIN_LEN = 8
ALLOW_ONE_MISSING_WORD = True  # set False to treat 'ANIL KUMAR' vs 'ANIL KUMAR SINGH' as a mismatch


def normalise(name: str | None) -> str:
    if not name:
        return ""
    s = unidecode(str(name)).upper()
    s = re.sub(r"[^A-Z ]", " ", s)
    toks = [t for t in s.split() if t not in HONORIFICS]
    return " ".join(toks)


def _phonetic(tok: str) -> str:
    t = tok.replace("PH", "F").replace("W", "V").replace("Y", "I").replace("Z", "J")
    t = re.sub(r"(.)\1+", r"\1", t)  # collapse doubled letters
    t = re.sub(r"[AEIOU]+", lambda m: "A" if m.start() else m.group(0)[0], t)  # vowel runs -> A (keeps first letter)
    return t


def token_match(a: str, b: str) -> str | None:
    """Return a reason string if the two words are 'the same', else None."""
    if a == b:
        return "same"
    if len(a) == 1 or len(b) == 1:
        return "initial" if a[0] == b[0] else None
    if _phonetic(a) == _phonetic(b) and min(len(a), len(b)) >= 4:
        return "spelling variant"
    if min(len(a), len(b)) >= TYPO_MIN_LEN and Levenshtein.distance(a, b) <= 1:
        return "typo"
    return None


@dataclass
class NameResult:
    ok: bool
    score: float
    note: str


def compare_names(a: str | None, b: str | None) -> NameResult:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return NameResult(False, 0.0, "name missing on one side")
    score = round(fuzz.token_sort_ratio(na, nb), 1)
    if na == nb:
        return NameResult(True, 100.0, "exact")
    ta, tb = na.split(), nb.split()
    if sorted(ta) == sorted(tb):
        return NameResult(True, 100.0, "word order differs")
    if na.replace(" ", "") == nb.replace(" ", ""):
        return NameResult(True, 100.0, "spacing differs")
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(long_) - len(short) > 1 or (len(long_) != len(short) and (len(short) < 2 or not ALLOW_ONE_MISSING_WORD)):
        return NameResult(False, score, "different number of words")
    used, notes = set(), []
    for w in short:
        hit = None
        for i, v in enumerate(long_):
            if i in used:
                continue
            r = token_match(w, v)
            if r:
                hit = (i, r)
                if r == "same":
                    break
        if not hit:
            return NameResult(False, score, "words differ")
        used.add(hit[0])
        if hit[1] != "same":
            notes.append(hit[1])
    note = "; ".join(sorted(set(notes))) or "same words"
    if len(long_) != len(short):
        note = "one word missing on one side" + (f" ({note})" if notes else "")
    return NameResult(True, score, note)
