"""PostgreSQL key/value storage (local Postgres, Docker Compose or Supabase).

Error messages and log lines produced here never include the connection string, credentials, SQL
parameter values or record content. Failures are logged as a structured category plus the psycopg
exception type and SQLSTATE only.
"""
import json
import logging
from datetime import datetime, timezone
from urllib.parse import unquote

import psycopg
from psycopg.conninfo import conninfo_to_dict

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db", ""}
EXPORT_KEYS = ("resume", "evidence", "interpretations", "last_job", "tracked_jobs")
CONNECT_TIMEOUT_SECONDS = 10
PLACEHOLDER_MARKERS = ("[YOUR-PASSWORD]", "YOUR-PASSWORD", "YOUR-PROJECT-REF", "YOUR-REGION", "<", ">")

SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS records (
    key TEXT PRIMARY KEY, content TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
# Supabase exposes the public schema through its Data API. Row level security with no policies
# blocks API (anon/authenticated) access while the owning database role used by this app keeps access.
RLS_SQL = "ALTER TABLE records ENABLE ROW LEVEL SECURITY"

logger = logging.getLogger("careerpilot.db")

HINTS = {
    "configuration": "DATABASE_URL is missing or malformed. Check the value in Streamlit secrets "
                     "(quoted, no [brackets] around the password, special characters URL-encoded).",
    "authentication": "Database rejected the credentials. With the Supabase Session pooler the user must be "
                      "postgres.<project-ref>, and the password must be URL-encoded in DATABASE_URL.",
    "dns": "Database host name could not be resolved. Copy the Session pooler host from Supabase > Connect.",
    "network": "Database host is unreachable. The Supabase direct host (db.<ref>.supabase.co) is IPv6-only on "
               "the Free plan; use the IPv4 Session pooler host (aws-0-<region>.pooler.supabase.com:5432).",
    "ssl": "SSL negotiation with the database failed. Supabase requires sslmode=require.",
    "timeout": "Database connection timed out. If you use Supabase Free, the project may be paused after a week "
               "of inactivity: restore it from the Supabase dashboard, then retry.",
    "schema": "Connected, but a database statement failed (missing table or insufficient privileges).",
    "unknown": "Database request failed. Verify DATABASE_URL and that the database is running.",
}

_MESSAGE_RULES = (
    ("authentication", ("password authentication failed", "tenant or user not found", "no password supplied",
                        "role \"", "authentication failed")),
    ("configuration", ("invalid connection option", "invalid uri", "invalid integer value", "missing \"=\"",
                       "invalid sslmode", "database \"")),
    ("dns", ("could not translate host name", "name or service not known", "nodename nor servname",
             "temporary failure in name resolution", "no address associated")),
    ("ssl", ("ssl",)),
    ("timeout", ("timeout expired", "timed out")),
    ("network", ("network is unreachable", "cannot assign requested address", "no route to host",
                 "connection refused")),
)


class StorageError(Exception):
    """Database problem described without secrets; `hint` is safe to show to the user."""

    def __init__(self, hint, category="unknown", exc_type="", sqlstate=None):
        super().__init__(hint)
        self.hint = hint
        self.category = category
        self.exc_type = exc_type
        self.sqlstate = sqlstate


def classify_error(exc):
    """Map an exception to a category; the raw message is inspected here but never logged or returned."""
    sqlstate = getattr(exc, "sqlstate", None) or ""
    if sqlstate.startswith("28"):
        return "authentication"
    if sqlstate == "3D000":  # database does not exist
        return "configuration"
    if sqlstate.startswith("42"):
        return "schema"
    # Connection failures usually carry no SQLSTATE: refine from the message (never logged).
    text = str(exc).lower()
    for category, needles in _MESSAGE_RULES:
        if any(needle in text for needle in needles):
            return category
    if isinstance(exc, psycopg.ProgrammingError) and not sqlstate:
        return "configuration"
    return "unknown"


def describe_connection_error(exc):
    return HINTS[classify_error(exc)]


def connection_profile(url):
    """Non-sensitive facts about DATABASE_URL, safe to log (no host, user, password or database names)."""
    profile = {"present": bool(url), "parseable": False, "placeholder_detected": False}
    if not url:
        return profile
    profile["placeholder_detected"] = any(marker in url for marker in PLACEHOLDER_MARKERS)
    profile["surrounding_quotes_or_space"] = url != url.strip() or url[:1] in "\"'" or url[-1:] in "\"'"
    profile["scheme"] = url.split("://", 1)[0] if "://" in url else "keyword-format"
    try:
        params = conninfo_to_dict(url)
    except Exception:
        return profile
    host = (params.get("host") or "").split(",")[0].lower()
    port = str(params.get("port") or "5432")
    user = unquote(params.get("user") or "")
    if host.endswith(".pooler.supabase.com"):
        host_kind = {"5432": "supabase_session_pooler", "6543": "supabase_transaction_pooler"}.get(port,
                                                                                                  "supabase_pooler")
    elif host.endswith(".supabase.co"):
        host_kind = "supabase_direct_ipv6_only"
    elif host in LOCAL_HOSTS:
        host_kind = "local"
    else:
        host_kind = "other"
    profile.update({
        "parseable": True,
        "host_kind": host_kind,
        "port": port,
        "has_user": bool(user),
        "user_has_project_ref": "." in user,
        "has_password": bool(params.get("password")),
        "url_sslmode": params.get("sslmode", "unset"),
    })
    return profile


def connection_kwargs(url, sslmode_override=""):
    """Build psycopg connect() keyword arguments, requiring SSL for any non-local host."""
    if not url:
        raise StorageError("DATABASE_URL is not configured. Add it to Streamlit secrets or the environment.",
                           "configuration", "MissingSetting")
    try:
        params = conninfo_to_dict(url)
    except Exception as exc:
        raise StorageError(HINTS["configuration"], "configuration", type(exc).__name__) from None
    host = (params.get("host") or "").split(",")[0]
    kwargs = {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": "careerpilot",
        # Supabase's transaction pooler (port 6543) does not support prepared statements; harmless for
        # the session pooler (port 5432) and direct connections.
        "prepare_threshold": None,
    }
    if sslmode_override:
        kwargs["sslmode"] = sslmode_override
    elif "sslmode" not in params:
        kwargs["sslmode"] = "prefer" if host in LOCAL_HOSTS else "require"
    return kwargs


def log_storage_error(operation, error):
    logger.error("database_error operation=%s category=%s exc_type=%s sqlstate=%s",
                 operation, error.category, error.exc_type, error.sqlstate or "none")


class Storage:
    def __init__(self, url, sslmode_override="", connect=psycopg.connect):
        self._url = url
        try:
            self._kwargs = connection_kwargs(url, sslmode_override)
        except StorageError as exc:
            log_storage_error("configure", exc)
            raise
        self._connect = connect

    def __repr__(self):
        return "Storage(<redacted>)"

    @property
    def effective_sslmode(self):
        return self._kwargs.get("sslmode", "from-url")

    def _run(self, sql, params=(), fetch=False, operation="query"):
        stage = "connect"
        try:
            with self._connect(self._url, **self._kwargs) as conn:
                stage = operation
                cursor = conn.execute(sql, params)
                return cursor.fetchall() if fetch else None
        except psycopg.Error as exc:
            category = classify_error(exc)
            error = StorageError(HINTS[category], category, type(exc).__name__, getattr(exc, "sqlstate", None))
            log_storage_error(stage, error)
            raise error from None

    def check_connection(self):
        """Open a connection and run SELECT 1; raises StorageError on failure."""
        self._run("SELECT 1", fetch=True, operation="check_connection")

    def init_schema(self):
        self._run(SCHEMA_SQL, operation="init_schema")
        try:
            self._run(RLS_SQL, operation="enable_rls")
        except StorageError:
            # Not the table owner (e.g. pre-existing table): the app still works; RLS is defence in depth.
            logger.warning("database_warning operation=enable_rls detail=skipped")

    def fetch(self, key):
        rows = self._run("SELECT content FROM records WHERE key = %s", (key,), fetch=True, operation="fetch")
        return rows[0][0] if rows else ""

    def save(self, key, value):
        self._run("""INSERT INTO records(key, content, updated_at)
           VALUES(%s, %s, NOW()) ON CONFLICT(key)
           DO UPDATE SET content=EXCLUDED.content, updated_at=NOW()""", (key, value), operation="save")

    def delete(self, key):
        self._run("DELETE FROM records WHERE key=%s", (key,), operation="delete")

    def load_jobs(self):
        try:
            jobs = json.loads(self.fetch("tracked_jobs") or "[]")
        except json.JSONDecodeError:
            return []
        return jobs if isinstance(jobs, list) else []

    def add_job(self, company, role, url, status):
        jobs = self.load_jobs()
        jobs.append({"company": company, "role": role, "url": url, "status": status,
                     "saved": datetime.now(timezone.utc).isoformat()})
        self.save("tracked_jobs", json.dumps(jobs))
        return jobs

    def export_all(self):
        """Every saved record (resume, evidence, interpretations, last job, tracked jobs) as a dict."""
        rows = self._run("SELECT key, content, updated_at FROM records WHERE key = ANY(%s) ORDER BY key",
                         (list(EXPORT_KEYS),), fetch=True, operation="export")
        records = {}
        for key, content, updated_at in rows:
            value = content
            if key == "tracked_jobs":
                try:
                    value = json.loads(content or "[]")
                except json.JSONDecodeError:
                    pass
            records[key] = {"content": value, "updated_at": updated_at.isoformat() if updated_at else None}
        return {"app": "CareerPilot AI", "exported_at": datetime.now(timezone.utc).isoformat(),
                "records": records}
