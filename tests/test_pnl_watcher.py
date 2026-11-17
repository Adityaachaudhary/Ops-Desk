from pathlib import Path

from app.pnl import watcher


def test_inbox_queues_stable_file_once(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "daily.csv"
    source.write_text(
        "Date,FC,Revenue,Manpower,Packaging,Power & Fuel,FC Rent,Equipment Rentals,Overheads\n"
        "2026-11-17,FC Test,100000,26000,6000,11000,16500,6500,11000\n",
        encoding="utf-8",
    )
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(watcher, "UPLOAD_DIR", upload_dir)
    created = []

    def create_job(*args, **kwargs):
        created.append((args, kwargs))
        return f"job_inbox_{len(created)}"

    monkeypatch.setattr(watcher.jobs, "create_job", create_job)
    scanner = watcher.InboxWatcher(inbox)

    scanner.scan_once()
    assert not created
    scanner.scan_once()
    assert len(created) == 1
    assert created[0][0][0] == "pnl"
    assert created[0][0][3]["send"] is False
    assert Path(created[0][0][2]).is_dir()

    scanner.scan_once()
    restarted_scanner = watcher.InboxWatcher(inbox)
    restarted_scanner.scan_once()
    restarted_scanner.scan_once()
    assert len(created) == 1


def test_inbox_stages_new_content_as_a_new_job(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    source = inbox / "daily.csv"
    source.write_text(
        "Date,FC,Revenue,Manpower,Packaging,Power & Fuel,FC Rent,Equipment Rentals,Overheads\n"
        "2026-11-17,FC Test,100000,26000,6000,11000,16500,6500,11000\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(watcher, "UPLOAD_DIR", tmp_path / "uploads")
    created = []
    monkeypatch.setattr(watcher.jobs, "create_job", lambda *args, **kwargs: created.append(args) or f"job_{len(created)}")
    scanner = watcher.InboxWatcher(inbox)
    scanner.scan_once()
    scanner.scan_once()

    source.write_text(source.read_text(encoding="utf-8").replace("100000", "101000"), encoding="utf-8")
    scanner.scan_once()
    scanner.scan_once()

    assert len(created) == 2
