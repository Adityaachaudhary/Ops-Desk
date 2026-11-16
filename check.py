"""Self-check for this installation:  python check.py
Compiles every source file, loads every template, then drives all three modules through the real app
(temporary database - your data is not touched). Prints PASS/FAIL per step and the exact traceback for a failure."""
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ["PROZO_DATA_DIR"] = tmp
os.environ["PROZO_OUTPUT_DIR"] = tmp + "/out"
bad = 0


def step(name, fn):
    global bad
    try:
        out = fn()
        print(f"PASS  {name}" + (f"  ({out})" if out else ""))
    except Exception:
        bad += 1
        print(f"FAIL  {name}\n" + "".join("      " + l for l in traceback.format_exc().splitlines(True)))


def compile_all():
    import py_compile
    n = 0
    for p in list((ROOT / "app").rglob("*.py")):
        py_compile.compile(str(p), doraise=True)
        n += 1
    return f"{n} files"


def templates():
    from app.core.web import templates as t
    names = [p.name for p in (ROOT / "app/web/templates").glob("*.html")]
    for n in names:
        t.env.get_template(n)
    return f"{len(names)} templates"


step("python files compile (syntax)", compile_all)
step("templates load (Jinja syntax)", templates)

from fastapi.testclient import TestClient  # noqa: E402

try:
    from app.main import app  # noqa: E402
except Exception:
    print("FAIL  import app.main\n" + traceback.format_exc())
    sys.exit(1)


def wait(c, jid, t=240):
    for _ in range(t * 2):
        j = c.get("/api/jobs/" + jid).json()
        if j["status"] not in ("queued", "processing"):
            return j
        time.sleep(0.5)
    raise RuntimeError("job did not finish in time")


def get(c, url, **kw):
    r = c.get(url, **kw)
    assert r.status_code == 200, f"HTTP {r.status_code} for {url}: {r.text[:300]}"
    return r


with TestClient(app, raise_server_exceptions=True) as c:
    from app.core.web import MODULES
    for k, m in MODULES.items():
        step(f"module '{k}' loaded", lambda m=m: (_ for _ in ()).throw(RuntimeError(m["error"])) if not m["enabled"] else "enabled")
    for p in ("/", "/jobs", "/parcel", "/compliance", "/pnl"):
        step(f"page {p}", lambda p=p: get(c, p) and "")

    def sample(url):
        r = c.post(url, follow_redirects=False)
        assert r.status_code == 303, f"{url} -> HTTP {r.status_code}: {r.text[:300]}"
        jid = r.headers["location"].split("/")[-1]
        j = wait(c, jid)
        assert j["status"].startswith("completed"), f"job {j['status']}: {j['message']}"
        return jid, j

    step("compliance sample run", lambda: (lambda x: (get(c, f"/compliance/jobs/{x[0]}"), x[1]["message"])[1])(sample("/compliance/sample")))

    def pnl():
        jid, j = sample("/pnl/sample")
        for tab in ("dashboard", "anomalies", "daily", "digests", "rules"):
            get(c, f"/pnl/jobs/{jid}?tab={tab}")
        get(c, f"/pnl/jobs/{jid}/digest", params={"fc": "FC Gurugram", "day": "2026-10-30"})
        get(c, f"/pnl/jobs/{jid}/download")
        return j["message"]

    step("P&L sample run + all tabs + digest preview + download", pnl)
    step("parcel page / OCR availability", lambda: "OCR ready" if "OCR engine not available" not in get(c, "/parcel").text else (_ for _ in ()).throw(RuntimeError("Tesseract not found - see README")))
    print("\nALL CHECKS PASSED" if not bad else f"\n{bad} CHECK(S) FAILED - send me the FAIL block above")