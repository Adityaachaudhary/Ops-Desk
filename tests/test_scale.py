import json

import pytest

pytest.importorskip("fpdf")
from app.compliance.parser import parse_esic_ecr, parse_pf_ecr, parse_wagesheet  # noqa: E402
from app.compliance.verifier import verify  # noqa: E402
from tests import make_big_ecr  # noqa: E402


def test_multi_page_ecr_against_ground_truth():
    n, _ = make_big_ecr.make(1500, seed=11)
    d = make_big_ecr.OUT
    ws, pf, es = parse_wagesheet(d / "wagesheet_big.xlsx"), parse_pf_ecr(d / "PF_ECR_big.pdf"), parse_esic_ecr(d / "ESIC_ECR_big.pdf")
    assert pf.pages >= 25 and not pf.warnings and not es.warnings and not pf.unparsed and not es.unparsed
    res, _ = verify(ws, pf, es)
    truth = json.loads((d / "ground_truth.json").read_text())
    for r in res:
        for kind, key in (("PF", "pf_status"), ("ESIC", "esic_status")):
            t = truth.get(r["uan"])
            exp = t[1] if t and t[0] == kind else "Matched"
            if exp == "Name mismatch":
                continue  # random replacement can create a legitimate 'one word missing' variant (documented)
            assert r[key] == exp, (r["employee"], kind, r[key], exp)
