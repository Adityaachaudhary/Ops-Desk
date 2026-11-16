"""End-to-end through the real app (workers + SQLite) in a temp data dir."""
import os
import re
import stat
import tempfile
import time

import pytest

os.environ["PROZO_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PROZO_OUTPUT_DIR"] = tempfile.mkdtemp()
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.core import db  # noqa: E402
from app.core.config import OUTPUT_DIR, UPLOAD_DIR  # noqa: E402


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


def test_job_created_time_is_marked_as_utc_for_local_display(client):
    jid = "job_timestamp_display"
    timestamp = "2026-11-16 16:29:26"
    db.execute(
        "INSERT INTO jobs(id,case_type,title,status,meta,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (jid, "parcel", "Timestamp test", "completed", "{}", timestamp, timestamp),
    )
    try:
        page = client.get("/jobs")
        assert "Created (local)" in page.text
        assert f'<time datetime="2026-11-16T16:29:26Z" data-local-time>{timestamp}</time>' in page.text
    finally:
        db.execute("DELETE FROM jobs WHERE id=?", (jid,))


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


def test_recent_case_jobs_can_be_deleted_with_all_outputs(client):
    for case_type in ("parcel", "compliance", "pnl"):
        jid = f"job_delete_{case_type}"
        input_dir = UPLOAD_DIR / jid
        input_dir.mkdir()
        (input_dir / "input.txt").write_text("test")
        suffix = ".csv" if case_type == "parcel" else ".xlsx"
        output_path = OUTPUT_DIR / f"{case_type}_{jid}{suffix}"
        output_path.write_text("test")
        extra_outputs = []
        if case_type == "compliance":
            extra_outputs.append(OUTPUT_DIR / f"compliance_{jid}.csv")
        elif case_type == "pnl":
            extra_outputs.append(OUTPUT_DIR / f"pnl_anomalies_{jid}.csv")
            digest_dir = OUTPUT_DIR / "digests" / jid
            digest_dir.mkdir(parents=True)
            digest_file = digest_dir / "digest.html"
            digest_file.write_text("test")
            digest_file.chmod(digest_file.stat().st_mode & ~stat.S_IWRITE)
            digest_dir.chmod(digest_dir.stat().st_mode & ~stat.S_IWRITE)
        for path in extra_outputs:
            path.write_text("test")
        db.execute(
            "INSERT INTO jobs(id,case_type,title,status,input_dir,output_path,meta,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (jid, case_type, "Delete test", "completed", str(input_dir), str(output_path), "{}", "now", "now"),
        )
        for table in ("parcel_results", "compliance_results", "fc_daily_records", "fc_anomalies", "fc_alerts", "job_events"):
            db.execute(f"INSERT INTO {table}(job_id) VALUES(?)", (jid,))

        for page_path in ("/", "/jobs", f"/{case_type}"):
            page = client.get(page_path)
            assert f'class="job-delete-form" method="post" action="/jobs/{jid}/delete"' in page.text

        response = client.post(f"/jobs/{jid}/delete", headers={"X-Requested-With": "XMLHttpRequest"})
        assert response.status_code == 200
        assert response.json() == {"deleted": True}
        assert "location" not in response.headers
        assert db.one("SELECT id FROM jobs WHERE id=?", (jid,)) is None
        assert not input_dir.exists()
        assert not output_path.exists()
        assert all(not path.exists() for path in extra_outputs)
        if case_type == "pnl":
            assert not digest_dir.exists()
        for table in ("parcel_results", "compliance_results", "fc_daily_records", "fc_anomalies", "fc_alerts", "job_events"):
            assert db.one(f"SELECT job_id FROM {table} WHERE job_id=?", (jid,)) is None

    active_jid = "job_delete_active"
    db.execute(
        "INSERT INTO jobs(id,case_type,title,status,meta,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (active_jid, "parcel", "Active job", "processing", "{}", "now", "now"),
    )
    page = client.get("/parcel")
    assert f'aria-label="Delete Active job"' in page.text
    assert f'action="/jobs/{active_jid}/delete"' not in page.text
    ajax_response = client.post(f"/jobs/{active_jid}/delete", headers={"X-Requested-With": "XMLHttpRequest"})
    assert ajax_response.status_code == 409
    assert "still running" in ajax_response.json()["error"]
    response = client.post(f"/jobs/{active_jid}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert db.one("SELECT id FROM jobs WHERE id=?", (active_jid,)) is not None
