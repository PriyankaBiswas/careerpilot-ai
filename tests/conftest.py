import os
from datetime import datetime, timezone

import psycopg
import pytest

from careerpilot.storage import Storage

SAMPLE_RESUME = "Jordan Example\nProject Manager, Example Corp (2020-2024)\n- Led migration of 3 internal tools to cloud."
SAMPLE_EVIDENCE = "Example Corp: migrated 3 internal tools to cloud in 2023; reduced hosting cost 18% (finance report)."
SAMPLE_JD = "We need a program manager with cloud migration and stakeholder management experience."


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConnection:
    """Minimal in-memory stand-in for the SQL statements Storage issues."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.db.statements.append(sql)
        text = " ".join(sql.split())
        if self.db.fail:
            raise psycopg.OperationalError(self.db.fail)
        if text.startswith(("CREATE TABLE", "ALTER TABLE")):
            return FakeCursor([])
        if text.startswith("SELECT content"):
            row = self.db.rows.get(params[0])
            return FakeCursor([(row[0],)] if row else [])
        if text.startswith("INSERT INTO records"):
            self.db.rows[params[0]] = (params[1], datetime.now(timezone.utc))
            return FakeCursor([])
        if text.startswith("DELETE FROM records"):
            self.db.rows.pop(params[0], None)
            return FakeCursor([])
        if text.startswith("SELECT key, content, updated_at"):
            keys = params[0]
            return FakeCursor([(k, v[0], v[1]) for k, v in sorted(self.db.rows.items()) if k in keys])
        raise AssertionError(f"Unexpected SQL: {text}")


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.statements = []
        self.connect_calls = []
        self.fail = None

    def connect(self, url, **kwargs):
        self.connect_calls.append((url, kwargs))
        return FakeConnection(self)


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def fake_store(fake_db):
    return Storage("postgresql://u:secret@db.example.com:5432/postgres", connect=fake_db.connect)


@pytest.fixture
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; skipping PostgreSQL integration test")
    with psycopg.connect(url) as conn:
        conn.execute("DROP TABLE IF EXISTS records")
    return url


# --- synthetic career data (fictional person and companies) -----------------------------------------

SYN_RESUME = """Jordan Example
jordan@example.test | Springfield
Senior Program Manager
EXPERIENCE
Senior Program Manager | Example Corp | Jan 2021 - Present
- Managed cloud migration program for internal tools
Program Analyst | Sample Industries | Jun 2017 - Dec 2020
- Coordinated vendor onboarding across 4 regions
SKILLS
Python, SQL, Jira, AWS
EDUCATION
BSc Business Information Systems, State University, 2017"""

SYN_RECORDS = [
    {"id": "E1", "verified": True, "title": "Cloud migration", "employer": "Example Corp",
     "role": "Senior Program Manager", "period": "2023", "ownership": "Individual",
     "details": "I led the migration of 3 internal tools to AWS and ran weekly stakeholder reviews.",
     "outcome": "Hosting cost fell 18% per the finance report."},
    {"id": "E2", "verified": True, "title": "Vendor onboarding platform", "employer": "Sample Industries",
     "role": "Program Analyst", "period": "2019", "ownership": "Team",
     "details": "Our team of 6 built a vendor onboarding portal; I wrote the requirements.",
     "outcome": "Onboarding time dropped from 10 days to 4 days."},
    {"id": "E3", "verified": False, "title": "Unconfirmed award", "employer": "Example Corp",
     "role": "", "period": "2024", "ownership": "Individual",
     "details": "Possibly nominated for an innovation award.", "outcome": ""},
]

SYN_JD = "Seeking a program manager with AWS cloud migration, stakeholder management and vendor management."


def claim(text, ids, quotes=(), support="verified", ownership="individual"):
    return {"text": text, "evidence_ids": list(ids), "evidence_quotes": list(quotes), "support": support,
            "ownership": ownership}


def synthetic_draft():
    """A ResumeDraft dict mixing grounded content with deliberate fabrications."""
    return {
        "contact_lines": ["Jordan Example", "jordan@example.test | Springfield", "Invented Phone 555-0100"],
        "headline": "Senior Program Manager",
        "summary": [
            claim("Program manager who led migration of 3 internal tools to AWS.", ["E1"], ["migration of 3 internal tools"]),
            claim("Cut costs by 40% across the company.", ["E1"], [], "verified"),  # fabricated metric
        ],
        "skills": [{"name": "AWS", "evidence_ids": ["R10"]}, {"name": "Kubernetes", "evidence_ids": []}],
        "experience": [
            {"employer": "Example Corp", "title": "Senior Program Manager", "dates": "Jan 2021 - Present",
             "location": None, "source_ids": ["R5"], "bullets": [
                 claim("Led migration of 3 internal tools to AWS, reducing hosting cost 18%.", ["E1"],
                       ["hosting cost fell 18%"]),
                 claim("Ran weekly stakeholder reviews.", ["E1"], ["weekly stakeholder reviews"]),
                 claim("Managed a $2M budget.", ["E9"], []),  # unknown source
             ]},
            {"employer": "Sample Industries", "title": "Program Analyst", "dates": "Jun 2017 - Dec 2020",
             "location": None, "source_ids": ["R7"], "bullets": [
                 claim("Built a vendor onboarding portal that cut onboarding from 10 to 4 days.", ["E2"],
                       ["onboarding time dropped from 10 days to 4 days"]),  # team work claimed as individual
                 claim("Contributed requirements to a team-built vendor onboarding portal.", ["E2"],
                       ["I wrote the requirements"], ownership="team"),
             ]},
            {"employer": "Imaginary Labs", "title": "Director", "dates": "2015 - 2017", "location": None,
             "source_ids": [], "bullets": [claim("Directed a team of 50.", [], [])]},
        ],
        "education": ["BSc Business Information Systems, State University, 2017", "PhD, Fake University"],
        "certifications": [],
        "gaps": ["No evidence of formal vendor management certification"],
        "notes": [],
    }


def spoken(words):
    return " ".join(["word"] * words)


def synthetic_pack():
    return {
        "answers": [{
            "question": "Tell me about a cloud migration you led.",
            "why_asked": "Tests delivery ownership.",
            "evidence_ids": ["E1"],
            "situation": "Example Corp ran internal tools on ageing servers.",
            "task": "I was asked to move 3 internal tools to AWS.",
            "action": "I led the migration and ran weekly stakeholder reviews.",
            "result": "Hosting cost fell 18% per the finance report.",
            "spoken_version": spoken(180),
            "follow_up_questions": ["What went wrong?", "How did you manage risk?"],
            "missing_evidence": [],
            "claims": [claim("I led the migration of 3 internal tools to AWS.", ["E1"], ["migration of 3 internal tools"])],
        }, {
            "question": "Describe a time you improved a process.",
            "why_asked": "Tests impact.",
            "evidence_ids": ["E2"],
            "situation": "Vendor onboarding was slow.",
            "task": "Improve onboarding.",
            "action": "I built the portal myself.",
            "result": "Onboarding time dropped by 75%.",  # fabricated percentage
            "spoken_version": spoken(60),  # too short
            "follow_up_questions": ["What was your role?"],
            "missing_evidence": ["Candidate's exact contribution beyond requirements"],
            "claims": [claim("I built the vendor onboarding portal.", ["E2"], [], ownership="individual")],
        }],
        "evidence_gaps": ["Budget ownership"],
    }
