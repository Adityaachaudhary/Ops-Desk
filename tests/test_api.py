"""End-to-end through the real app (workers + SQLite) in a temp data dir."""
import os
import re
import tempfile
import time

import pytest

os.environ["PROZO_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PROZO_OUTPUT_DIR"] = tempfile.mkdtemp()
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait(c, jid, t=90):
    for _ in range(t * 2):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] not in ("queued", "processing"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def err(r):
    return " ".join(re.findall(r'class="banner err"[^>]*>([^<]+)', r.text))


def test_pages_load(client):
    for p in ("/", "/parcel", "/compliance", "/pnl", "/jobs", "/api/health"):
        assert client.get(p).status_code == 200
    assert client.get("/nope").status_code == 404


def test_compliance_sample_and_bad_input(client):
    jid = client.post("/compliance/sample", follow_redirects=False).headers["location"].split("/")[-1]
    j = wait(client, jid)
    assert j["status"] == "completed" and "6 fully compliant" in j["message"]
    assert client.get(f"/compliance/jobs/{jid}/download").status_code == 200
    assert "required" in err(client.post("/compliance/upload", data={"tolerance": "1"}))
    r = client.post("/compliance/upload", files={"wagesheet": ("a.xlsx", b"junk"), "pf": ("p.pdf", b"%PDF junk"), "esic": ("e.pdf", b"%PDF junk")})
    assert "could not be opened" in err(r)


def test_pnl_sample_and_bad_input(client):
    jid = client.post("/pnl/sample", follow_redirects=False).headers["location"].split("/")[-1]
    j = wait(client, jid)
    assert j["status"] == "completed"
    for tab in ("dashboard", "anomalies", "daily", "digests", "rules"):
        assert client.get(f"/pnl/jobs/{jid}?tab={tab}").status_code == 200
    assert "empty" in err(client.post("/pnl/upload", files={"file": ("x.csv", b"")}))
    assert "Could not find the data table" in err(client.post("/pnl/upload", files={"file": ("x.csv", b"a,b\n1,2\n")}))


def test_parcel_rejects_empty_and_wrong_type(client):
    r = client.post("/parcel/upload", files=[("files", ("x.jpg", b""))])
    assert err(r)  # either OCR-unavailable message or "is empty" - never a crash
    assert r.status_code == 200
