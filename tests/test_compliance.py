from decimal import Decimal
from pathlib import Path

from app.compliance.parser import parse_esic_ecr, parse_pf_ecr, parse_wagesheet, clean_id
from app.compliance.verifier import verify

S = Path(__file__).resolve().parents[1] / "data" / "sample" / "case2"


def _run():
    ws = parse_wagesheet(S / "Sample_Wagesheet_Aug25.xlsx")
    pf = parse_pf_ecr(S / "PF ECR AUG-25 SAMPLE.pdf")
    es = parse_esic_ecr(S / "ESIC ECR AUG-25 SAMPLE.pdf")
    res, extras = verify(ws, pf, es)
    return {r["employee"]: r for r in res}, pf, es


def test_sample_verdicts():
    r, pf, es = _run()
    assert (len(pf.rows), len(es.rows)) == (11, 10) and not pf.warnings and not es.warnings
    assert r["ANIL KUMAR"]["pf_status"] == r["ANIL KUMAR"]["esic_status"] == "Matched"
    assert r["DINESH PRASAD"]["pf_status"] == "Name mismatch" and r["DINESH PRASAD"]["esic_status"] == "Matched"
    assert r["REKHA SHARMA"]["pf_status"] == "Matched" and r["REKHA SHARMA"]["esic_status"] == "Name mismatch"
    assert r["SANJAY PASWAN"]["pf_status"] == r["SANJAY PASWAN"]["esic_status"] == "Missing"
    assert r["POOJA GUPTA"]["pf_status"] == "Matched" and r["POOJA GUPTA"]["esic_status"] == "Missing"
    v = r["VIKRAM THAKUR"]
    assert v["pf_status"] == "Amount mismatch" and v["pf_wagesheet"] != v["pf_ecr"] and v["pf_diff"]
    n = r["NEHA MISHRA"]
    assert n["esic_status"] == "Amount mismatch" and (n["esic_wagesheet"], n["esic_ecr"], n["esic_diff"]) == ("86", "66", "-20")


def test_id_cleaning_keeps_strings():
    assert clean_id(999912340001.0) == "999912340001" and clean_id("0012345678") == "0012345678" and clean_id("nan") == ""
