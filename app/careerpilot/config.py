"""Settings lookup: Streamlit secrets first (Community Cloud), then environment variables (Docker/local).

Values are never logged or displayed; diagnostics report only where a setting came from.
"""
import os

AI_PROVIDERS = ("manual", "openai")
DEFAULT_AI_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-6.1-terra"
DEFAULT_OPENAI_FAST_MODEL = "gpt-6-luna"
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
DEFAULT_MAX_COST_PER_OPERATION = 0.25
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
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
    """Return 'openai' (default: in-app generation) or 'manual' (copy/paste fallback, no API key)."""
    value = get_setting("AI_PROVIDER", DEFAULT_AI_PROVIDER, **kwargs).lower()
    if value not in AI_PROVIDERS:
        raise ValueError(f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)}.")
    return value


def openai_settings(**kwargs):
    """OpenAI options with safe defaults. Raises ValueError for invalid numbers or effort levels."""
    def number(name, default, cast, low, high):
        raw = get_setting(name, "", **kwargs)
        try:
            value = cast(raw) if raw else default
        except ValueError:
            raise ValueError(f"{name} must be a number.") from None
        return min(max(value, low), high)

    def effort(name, default):
        value = get_setting(name, default, **kwargs).lower()
        if value not in REASONING_EFFORTS:
            raise ValueError(f"{name} must be one of {', '.join(REASONING_EFFORTS)}.")
        return value

    fast = get_setting("OPENAI_FAST_MODEL", DEFAULT_OPENAI_FAST_MODEL, **kwargs)
    model = get_setting("OPENAI_MODEL", DEFAULT_OPENAI_MODEL, **kwargs)
    return {
        "model": model,
        # Set OPENAI_FAST_MODEL = "none" to use the main model for matching and evidence checks.
        "fast_model": model if fast.lower() == "none" else fast,
        "max_output_tokens": number("OPENAI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS, int, 1_000, 128_000),
        "max_cost": number("OPENAI_MAX_COST_PER_OPERATION", DEFAULT_MAX_COST_PER_OPERATION, float, 0.0, 50.0),
        "reasoning_effort": effort("OPENAI_REASONING_EFFORT", "medium"),
        "fast_reasoning_effort": effort("OPENAI_FAST_REASONING_EFFORT", "low"),
        "prices_json": get_setting("OPENAI_PRICES", "", **kwargs),
    }


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
