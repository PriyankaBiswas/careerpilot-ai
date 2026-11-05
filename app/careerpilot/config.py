"""Settings lookup: Streamlit secrets first (Community Cloud), then environment variables (Docker/local).

Values are never logged or displayed; diagnostics report only where a setting came from.
"""
import os

AI_PROVIDERS = ("manual", "openai")
DEFAULT_AI_PROVIDER = "manual"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
MIN_PASSWORD_LENGTH = 16


def _streamlit_secrets():
    """Return (secrets mapping or None, status) where status is 'ok', 'missing' or 'invalid'."""
    try:
        import streamlit as st

        secrets = st.secrets
        list(secrets.keys())  # forces parsing so TOML errors surface here
        return secrets, "ok"
    except Exception as exc:
        # Missing file and TOML syntax errors both raise StreamlitSecretNotFoundError; only the latter
        # mentions parsing. The message itself is not returned because it can quote secrets content.
        return None, "invalid" if "pars" in str(exc).lower() else "missing"


def _from_streamlit_secrets(name):
    secrets, _ = _streamlit_secrets()
    if secrets is not None and name in secrets:
        value = secrets[name]
        return value if isinstance(value, str) else str(value)
    return None


def get_setting(name, default="", secrets_lookup=_from_streamlit_secrets, environ=None):
    environ = os.environ if environ is None else environ
    value = secrets_lookup(name)
    if value is None or not str(value).strip():
        value = environ.get(name, "")
    value = str(value).strip()
    return value if value else default


def setting_diagnostics(name, secrets_loader=_streamlit_secrets, environ=None):
    """Where a setting comes from, without its value: safe to log."""
    environ = os.environ if environ is None else environ
    secrets, status = secrets_loader()
    in_secrets = bool(secrets is not None and name in secrets and str(secrets[name]).strip())
    nested = False
    if secrets is not None and not in_secrets:
        # Common mistake: the key was pasted under a [section] header, so it is not at the TOML root.
        nested = any(hasattr(v, "keys") and name in v for v in secrets.values())
    if in_secrets:
        source = "secrets"
    elif environ.get(name, "").strip():
        source = "environment"
    else:
        source = "missing"
    return {"setting": name, "source": source, "secrets_file": status, "found_in_nested_section": nested}


def ai_provider(**kwargs):
    """Return 'manual' (default, no API key needed) or 'openai' (opt-in)."""
    value = get_setting("AI_PROVIDER", DEFAULT_AI_PROVIDER, **kwargs).lower()
    if value not in AI_PROVIDERS:
        raise ValueError(f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)}.")
    return value


def password_status(password):
    """Classify the optional APP_PASSWORD: 'disabled', 'weak' or 'enabled'."""
    if not password:
        return "disabled"
    if len(password) < MIN_PASSWORD_LENGTH:
        return "weak"
    return "enabled"


def configure_logging():
    """Send careerpilot.* log lines to stderr (shown in Streamlit Community Cloud's "Manage app" logs)."""
    import logging
    import sys

    logger = logging.getLogger("careerpilot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
