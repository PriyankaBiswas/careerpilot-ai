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
