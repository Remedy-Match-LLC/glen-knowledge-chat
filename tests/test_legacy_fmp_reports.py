import csv
import sqlite3

from dashboard import legacy_fmp_reports as legacy


def _csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def test_build_merges_same_day_and_augments_electronic(tmp_path):
    contacts = tmp_path / "contacts.csv"; records = tmp_path / "records.csv"; electronic = tmp_path / "electronic.csv"
    _csv(contacts, ["PtId#", "email", "First", "Last", "Full Name"],
         [{"PtId#": "7", "email": "A@X.com", "First": "Ada", "Last": "A"}])
    fields = ["PtId#", "TodDat", "head1", "remedy1A", "remedy1B"]
    _csv(records, fields, [{"PtId#": "7", "TodDat": "1/2/2020", "head1": "Liver", "remedy1A": "R1"},
                           {"PtId#": "7", "TodDat": "1/2/2020", "head1": "Kidney", "remedy1A": "R2"}])
    _csv(electronic, ["PtId#", "Date"], [{"PtId#": "7", "Date": "1/2/2020"}])
    payload = legacy.build_payload(records, electronic, contacts)
    assert len(payload["reports"]) == 1
    report = payload["reports"][0]
    assert report["email"] == "a@x.com" and len(report["content"]["layers"]) == 2
    assert report["content"]["legacy_source"]["electronic_rows"] == 1


def test_ingest_is_dry_run_idempotent_and_never_overwrites(tmp_path):
    cx = sqlite3.connect(tmp_path / "db.sqlite")
    payload = {"reports": [{"email": "a@x.com", "name": "Ada", "scan_date": "2020-01-02",
                            "content": {"layers": [{"title": "Liver"}]}}]}
    assert legacy.ingest_payload(cx, payload)["inserted"] == 1
    assert cx.execute("SELECT COUNT(*) FROM portal_biofield_reports").fetchone()[0] == 0
    first = legacy.ingest_payload(cx, payload, commit=True)
    assert first["inserted"] == 1 and first["portals_created"] == 1
    second = legacy.ingest_payload(cx, payload, commit=True)
    assert second["existing"] == 1 and second["inserted"] == 0
    assert cx.execute("SELECT COUNT(*) FROM portal_biofield_reports").fetchone()[0] == 1
