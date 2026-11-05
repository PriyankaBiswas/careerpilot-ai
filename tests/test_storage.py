import json

import pytest

import psycopg

from careerpilot.storage import (Storage, StorageError, classify_error, connection_kwargs, connection_profile,
                                describe_connection_error)

SUPABASE_URL = "postgresql://postgres.abcdef:p%40ss%21word@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"


# --- connection settings -------------------------------------------------------------------------

def test_remote_hosts_require_ssl_and_disable_prepared_statements():
    kwargs = connection_kwargs(SUPABASE_URL)
    assert kwargs["sslmode"] == "require"
    assert kwargs["prepare_threshold"] is None
    assert kwargs["connect_timeout"] > 0


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "db"])
def test_local_hosts_prefer_ssl(host):
    assert connection_kwargs(f"postgresql://u:p@{host}:5432/x")["sslmode"] == "prefer"


def test_explicit_sslmode_in_url_or_override_is_respected():
    assert "sslmode" not in connection_kwargs(SUPABASE_URL + "?sslmode=verify-full")
    assert connection_kwargs(SUPABASE_URL, "verify-full")["sslmode"] == "verify-full"


def test_missing_or_invalid_url_raises_without_leaking_it():
    with pytest.raises(StorageError, match="not configured"):
        connection_kwargs("")
    bad = "postgresql://user:topsecret@host:notaport/db?bogus"
    with pytest.raises(StorageError) as info:
        connection_kwargs(bad)
    assert "topsecret" not in str(info.value)


def test_repr_redacts_url(fake_db):
    store = Storage(SUPABASE_URL, connect=fake_db.connect)
    assert "p%40ss" not in repr(store) and "supabase" not in repr(store)


@pytest.mark.parametrize("message, expected", [
    ('connection to server failed: FATAL: password authentication failed for user "postgres.abc"', "authentication"),
    ("FATAL: Tenant or user not found", "authentication"),
    ("connection timeout expired", "timeout"),
    ('could not translate host name "db.abc.supabase.co" to address', "dns"),
    ("connection to server at 2a05::1, port 5432 failed: Network is unreachable", "network"),
    ("SSL SYSCALL error: EOF detected", "ssl"),
    ("invalid sslmode value: \"bogus\"", "configuration"),
    ("something else", "unknown"),
])
def test_error_classification(message, expected):
    assert classify_error(psycopg.OperationalError(message)) == expected
    hint = describe_connection_error(psycopg.OperationalError(message))
    assert "postgres.abc" not in hint and "2a05" not in hint


def test_sqlstate_classification_takes_precedence():
    class FakeError(psycopg.Error):
        sqlstate = None

    for sqlstate, expected in (("28P01", "authentication"), ("3D000", "configuration"), ("42P01", "schema")):
        err = FakeError("ssl looking message")
        err.sqlstate = sqlstate
        assert classify_error(err) == expected


def test_connection_profile_for_supabase_session_pooler_has_no_secrets():
    profile = connection_profile(SUPABASE_URL + "?sslmode=require")
    assert profile["host_kind"] == "supabase_session_pooler"
    assert profile["port"] == "5432" and profile["url_sslmode"] == "require"
    assert profile["user_has_project_ref"] and profile["has_password"]
    flat = repr(profile)
    for secret in ("abcdef", "p%40ss", "p@ss", "aws-0-eu-west-1", "postgres."):
        assert secret not in flat


@pytest.mark.parametrize("url, field, expected", [
    ("postgresql://postgres:pw@db.abcdef.supabase.co:5432/postgres", "host_kind", "supabase_direct_ipv6_only"),
    ("postgresql://postgres.x:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres", "host_kind",
     "supabase_transaction_pooler"),
    ("postgresql://postgres:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres", "user_has_project_ref", False),
    ("postgresql://postgres.x:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
     "placeholder_detected", True),
    ('"postgresql://u:p@h:5432/d"', "surrounding_quotes_or_space", True),
    ("", "present", False),
])
def test_connection_profile_flags_common_mistakes(url, field, expected):
    assert connection_profile(url)[field] == expected


# --- persistence operations (in-memory fake connection) -----------------------------------------

def test_init_schema_creates_table_and_enables_rls(fake_store, fake_db):
    fake_store.init_schema()
    assert any("CREATE TABLE IF NOT EXISTS records" in s for s in fake_db.statements)
    assert any("ENABLE ROW LEVEL SECURITY" in s for s in fake_db.statements)


def test_save_fetch_update_delete(fake_store):
    assert fake_store.fetch("resume") == ""
    fake_store.save("resume", "v1")
    fake_store.save("resume", "v2")
    assert fake_store.fetch("resume") == "v2"
    fake_store.delete("resume")
    assert fake_store.fetch("resume") == ""


def test_jobs_round_trip_and_tolerate_corrupt_data(fake_store):
    assert fake_store.load_jobs() == []
    fake_store.add_job("Acme", "PM", "https://acme.example/jobs/1", "Saved")
    jobs = fake_store.add_job("Beta", "TPM", "", "Applied")
    assert [j["company"] for j in jobs] == ["Acme", "Beta"]
    assert fake_store.load_jobs()[1]["status"] == "Applied"
    fake_store.save("tracked_jobs", "{not json")
    assert fake_store.load_jobs() == []


def test_export_all_includes_every_saved_record(fake_store):
    fake_store.save("resume", "resume text")
    fake_store.save("evidence", "evidence text")
    fake_store.save("interpretations", "my view")
    fake_store.add_job("Acme", "PM", "", "Saved")
    fake_store.save("unrelated", "ignored")
    export = fake_store.export_all()
    records = export["records"]
    assert set(records) == {"resume", "evidence", "interpretations", "tracked_jobs"}
    assert records["resume"]["content"] == "resume text"
    assert records["tracked_jobs"]["content"][0]["company"] == "Acme"
    json.dumps(export)  # must be serialisable for download


def test_driver_errors_become_safe_storage_errors(fake_store, fake_db):
    fake_db.fail = 'password authentication failed for user "postgres.secretref"'
    with pytest.raises(StorageError) as info:
        fake_store.fetch("resume")
    assert "secretref" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert info.value.category == "authentication" and info.value.exc_type == "OperationalError"


def test_error_logs_are_structured_and_contain_no_secrets(fake_db, caplog):
    store = Storage(SUPABASE_URL, connect=fake_db.connect)
    fake_db.fail = 'password authentication failed for user "postgres.abcdef" host aws-0-eu-west-1'
    with caplog.at_level("INFO", logger="careerpilot"), pytest.raises(StorageError):
        store.save("resume", "SENSITIVE RESUME TEXT")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "category=authentication" in logged and "exc_type=OperationalError" in logged
    for secret in ("abcdef", "p%40ss", "eu-west-1", "SENSITIVE", "resume", "password authentication"):
        assert secret not in logged


def test_connections_use_ssl_for_remote_database(fake_store, fake_db):
    fake_store.fetch("resume")
    _, kwargs = fake_db.connect_calls[0]
    assert kwargs["sslmode"] == "require"


# --- real PostgreSQL (runs when TEST_DATABASE_URL is set, e.g. in CI) ----------------------------

def test_postgres_integration(pg_url):
    store = Storage(pg_url)
    store.init_schema()
    store.init_schema()  # idempotent
    store.save("evidence", "verified fact")
    store.save("evidence", "verified fact v2")
    store.add_job("Acme", "PM", "", "Saved")
    assert store.fetch("evidence") == "verified fact v2"
    export = store.export_all()["records"]
    assert export["evidence"]["content"] == "verified fact v2"
    assert export["tracked_jobs"]["content"][0]["role"] == "PM"
    store.delete("evidence")
    assert store.fetch("evidence") == ""


# --- minimal real connection tests ----------------------------------------------------------------

def test_check_connection_succeeds(pg_url):
    Storage(pg_url).check_connection()


def test_real_dns_failure_is_classified():
    store = Storage("postgresql://postgres.ref:pw@careerpilot-no-such-host.invalid:5432/postgres")
    with pytest.raises(StorageError) as info:
        store.check_connection()
    assert info.value.category == "dns"
    assert info.value.exc_type == "OperationalError"


def test_real_refused_connection_is_classified():
    store = Storage("postgresql://u:pw@127.0.0.1:1/postgres")
    with pytest.raises(StorageError) as info:
        store.check_connection()
    assert info.value.category == "network"


def test_ssl_required_against_server_without_ssl(pg_url):
    # Local Homebrew/CI Postgres has no SSL, so forcing sslmode=require must fail with category "ssl".
    store = Storage(pg_url, sslmode_override="require")
    try:
        store.check_connection()
    except StorageError as exc:
        assert exc.category == "ssl"
    else:
        pytest.skip("Test server supports SSL")
