"""Saved master resumes, evidence records, resume versions and API usage (stored as JSON in `records`).

Legacy single-value keys ("resume", "evidence") are migrated on first read and left in place.
"""
import json
import uuid
from datetime import datetime, timezone

OWNERSHIP_CHOICES = ["Individual", "Team", "Shared", "Unspecified"]
EVIDENCE_FIELDS = ["id", "verified", "title", "employer", "role", "period", "ownership", "details", "outcome"]
MAX_USAGE_ROWS = 500


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_list(store, key):
    try:
        value = json.loads(store.fetch(key) or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def save_list(store, key, items):
    store.save(key, json.dumps(items))


# --- master resumes ---------------------------------------------------------------------------------

def load_resumes(store):
    resumes = load_list(store, "master_resumes")
    if not resumes:
        legacy = store.fetch("resume")
        if legacy.strip():
            resumes = [{"id": "legacy", "name": "Master resume", "text": legacy, "saved_at": _now()}]
            save_list(store, "master_resumes", resumes)
    return resumes


def add_resume(store, name, text):
    resumes = load_resumes(store)
    resumes.append({"id": uuid.uuid4().hex[:8], "name": name.strip() or f"Resume {len(resumes) + 1}",
                    "text": text, "saved_at": _now()})
    save_list(store, "master_resumes", resumes)
    store.save("resume", text)  # keep the legacy key current for manual mode and older exports
    return resumes


def delete_resume(store, resume_id):
    save_list(store, "master_resumes", [r for r in load_resumes(store) if r["id"] != resume_id])


# --- evidence records -------------------------------------------------------------------------------

def _blank_record(**values):
    record = {name: "" for name in EVIDENCE_FIELDS}
    record.update(verified=True, ownership="Unspecified")
    record.update(values)
    return record


def load_evidence(store):
    records = load_list(store, "evidence_records")
    if not records:
        legacy = store.fetch("evidence")
        paragraphs = [p.strip() for p in legacy.replace("\r\n", "\n").split("\n\n") if p.strip()]
        if paragraphs:
            records = [_blank_record(id=f"E{i}", title=f"Imported evidence {i}", details=p)
                       for i, p in enumerate(paragraphs, 1)]
            save_list(store, "evidence_records", records)
    return records


def normalize_evidence(rows):
    """Clean edited rows: keep known fields, drop empty rows, assign E-IDs to new rows."""
    cleaned, used = [], set()
    for row in rows:
        values = {k: row.get(k) for k in EVIDENCE_FIELDS if row.get(k) is not None and row.get(k) == row.get(k)}
        record = _blank_record(**values)  # (the x == x test drops NaN cells from new data-editor rows)
        record = {k: (v.strip() if isinstance(v, str) else v) for k, v in record.items()}
        if not any(record[k] for k in ("title", "details", "outcome")):
            continue
        record["verified"] = bool(record["verified"])
        if record["ownership"] not in OWNERSHIP_CHOICES:
            record["ownership"] = "Unspecified"
        if record["id"] in used or not str(record["id"]).startswith("E"):
            record["id"] = ""
        used.add(record["id"])
        cleaned.append(record)
    highest = max([int(r["id"][1:]) for r in cleaned if r["id"][1:].isdigit()] or [0])
    for record in cleaned:
        if not record["id"]:
            highest += 1
            record["id"] = f"E{highest}"
    return cleaned


def save_evidence(store, rows):
    records = normalize_evidence(rows)
    save_list(store, "evidence_records", records)
    return records


# --- resume versions --------------------------------------------------------------------------------

def save_version(store, job_label, resume_name, draft_json, approved_text, model, review_summary):
    versions = load_list(store, "resume_versions")
    version = {"id": uuid.uuid4().hex[:8], "job": job_label.strip() or "Untitled job", "resume": resume_name,
               "created_at": _now(), "model": model, "draft": draft_json, "approved_text": approved_text,
               "review": review_summary}
    versions.append(version)
    save_list(store, "resume_versions", versions)
    return version


def delete_version(store, version_id):
    save_list(store, "resume_versions", [v for v in load_list(store, "resume_versions") if v["id"] != version_id])


# --- usage ledger (metadata only: no prompts or outputs) --------------------------------------------

def record_usage(store, usage_record):
    rows = load_list(store, "api_usage")
    rows.append({"at": _now(), **usage_record})
    save_list(store, "api_usage", rows[-MAX_USAGE_ROWS:])
