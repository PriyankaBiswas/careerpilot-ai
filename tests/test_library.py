import json
import math

from careerpilot import library
from conftest import SYN_RECORDS, SYN_RESUME


def test_legacy_resume_and_evidence_are_migrated(fake_store):
    fake_store.save("resume", SYN_RESUME)
    fake_store.save("evidence", "Led migration of 3 tools.\n\nWrote vendor requirements.")
    resumes = library.load_resumes(fake_store)
    assert resumes[0]["name"] == "Master resume" and resumes[0]["text"] == SYN_RESUME
    records = library.load_evidence(fake_store)
    assert [r["id"] for r in records] == ["E1", "E2"] and records[1]["details"] == "Wrote vendor requirements."
    assert all(r["verified"] for r in records)
    assert json.loads(fake_store.fetch("evidence_records"))  # persisted once


def test_add_and_delete_resumes(fake_store):
    library.add_resume(fake_store, "PM resume", SYN_RESUME)
    resumes = library.add_resume(fake_store, "", "other text")
    assert [r["name"] for r in resumes] == ["PM resume", "Resume 2"]
    assert fake_store.fetch("resume") == "other text"
    library.delete_resume(fake_store, resumes[0]["id"])
    assert [r["name"] for r in library.load_resumes(fake_store)] == ["Resume 2"]


def test_normalize_evidence_assigns_ids_and_drops_empty_rows():
    rows = [dict(SYN_RECORDS[0]),
            {"id": None, "verified": float("nan"), "title": "New project", "details": "Did X",
             "ownership": "Bogus", "outcome": math.nan},
            {"id": "E1", "title": "Duplicate id", "details": "y"},
            {"id": None, "title": "", "details": "", "outcome": ""}]
    records = library.normalize_evidence(rows)
    assert [r["id"] for r in records] == ["E1", "E2", "E3"]
    assert records[1]["ownership"] == "Unspecified" and records[1]["outcome"] == ""
    assert records[1]["verified"] is True  # NaN cell falls back to the default


def test_versions_and_usage_ledger(fake_store):
    version = library.save_version(fake_store, "PM at Acme", "PM resume", {"summary": []}, "text", "gpt-6.1-terra",
                                   {"approved": 3, "excluded": 1})
    assert library.load_list(fake_store, "resume_versions")[0]["job"] == "PM at Acme"
    library.delete_version(fake_store, version["id"])
    assert library.load_list(fake_store, "resume_versions") == []
    for i in range(library.MAX_USAGE_ROWS + 5):
        library.record_usage(fake_store, {"operation": "x", "cost_usd": 0.001})
    rows = library.load_list(fake_store, "api_usage")
    assert len(rows) == library.MAX_USAGE_ROWS and set(rows[0]) == {"at", "operation", "cost_usd"}


def test_export_includes_new_collections(fake_store):
    library.add_resume(fake_store, "PM", SYN_RESUME)
    library.save_evidence(fake_store, SYN_RECORDS)
    records = fake_store.export_all()["records"]
    assert records["master_resumes"]["content"][0]["name"] == "PM"
    assert records["evidence_records"]["content"][0]["id"] == "E1"
