"""Manual-mode behaviour: no OpenAI key needed, config precedence, opt-in API path and the Streamlit app."""
import os
import sys
import types

import pytest
from streamlit.testing.v1 import AppTest

from careerpilot import ai_client
from careerpilot.config import ai_provider, get_setting, password_status, setting_diagnostics
from careerpilot.documents import docx_bytes, extract
from conftest import SAMPLE_EVIDENCE, SAMPLE_JD, SAMPLE_RESUME

APP = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")
no_secrets = lambda name: None  # noqa: E731


def test_manual_is_default_provider():
    assert ai_provider(secrets_lookup=no_secrets, environ={}) == "manual"
    assert ai_provider(secrets_lookup=no_secrets, environ={"AI_PROVIDER": " OpenAI "}) == "openai"
    with pytest.raises(ValueError):
        ai_provider(secrets_lookup=no_secrets, environ={"AI_PROVIDER": "gemini"})


def test_secrets_take_precedence_over_environment():
    assert get_setting("X", secrets_lookup=lambda n: "from-secrets", environ={"X": "env"}) == "from-secrets"
    assert get_setting("X", secrets_lookup=lambda n: "  ", environ={"X": "env"}) == "env"
    assert get_setting("X", "dflt", secrets_lookup=no_secrets, environ={}) == "dflt"


def test_password_is_optional_but_must_be_strong_when_set():
    assert password_status("") == "disabled"
    assert password_status("short") == "weak"
    assert password_status("x" * 16) == "enabled"


def test_api_mode_requires_key_and_passes_prompt():
    with pytest.raises(ai_client.AIConfigError):
        ai_client.generate("prompt", "", "model")
    calls = {}

    class FakeClient:
        def __init__(self, api_key, timeout):
            calls["key"] = api_key
            self.responses = types.SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(output_text="draft")

    assert ai_client.generate("the prompt", "k", "m", client_factory=FakeClient) == "draft"
    assert calls["input"] == "the prompt" and calls["model"] == "m" and calls["key"] == "k"


def test_manual_mode_does_not_import_openai():
    for module in [m for m in sys.modules if m == "openai" or m.startswith("openai.")]:
        sys.modules.pop(module)
    import careerpilot.prompts  # noqa: F401
    assert "openai" not in sys.modules


def test_document_round_trip():
    data = docx_bytes("Line one\n\nLine two")
    assert extract("draft.DOCX", data) == "Line one\nLine two"
    assert extract("r.txt", b"plain") == "plain"
    with pytest.raises(ValueError):
        extract("r.rtf", b"x")
    with pytest.raises(ValueError):
        extract("r.txt", b"x" * 5_000_001)


# --- Streamlit app ---------------------------------------------------------------------------------

@pytest.fixture
def app_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "APP_PASSWORD", "AI_PROVIDER", "DATABASE_URL", "DATABASE_SSLMODE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_app_without_database_shows_safe_error(app_env):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("DATABASE_URL is not configured" in e.value for e in at.error)


def test_app_rejects_weak_password(app_env):
    app_env.setenv("APP_PASSWORD", "short")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any("shorter than 16" in e.value for e in at.error)


def test_app_manual_flow_without_openai_key(app_env, pg_url):
    app_env.setenv("DATABASE_URL", pg_url)
    from careerpilot.storage import Storage
    store = Storage(pg_url)
    store.init_schema()
    store.save("resume", SAMPLE_RESUME)
    store.save("evidence", SAMPLE_EVIDENCE)

    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and not at.error
    assert any("AI mode: **manual**" in c.value for c in at.sidebar.caption)

    jd_area = next(t for t in at.text_area if t.label == "Paste the job description")
    jd_area.input(SAMPLE_JD)
    next(b for b in at.button if b.label == "Build resume prompt").click()
    at.run()
    assert not at.exception and not at.error
    prompt = at.session_state["resume_prompt"]
    assert SAMPLE_EVIDENCE in prompt and SAMPLE_JD in prompt
    assert any(SAMPLE_JD in c.value for c in at.code)

    paste = next(t for t in at.text_area if t.label == "Paste the AI result here")
    paste.input("Tailored resume\nLed migration [VERIFY: year?]")
    next(b for b in at.button if b.label == "Load pasted result for review").click()
    at.run()
    assert at.session_state["resume_edit"].startswith("Tailored resume")
    assert any("[VERIFY] marker" in w.value for w in at.warning)

    download = at.get("download_button")
    resume_download = next(d for d in download if d.label == "Download editable Word draft")
    assert resume_download.proto.disabled
    for box in at.checkbox:
        if box.key.startswith("resume_check_"):
            box.check()
    at.run()
    resume_download = next(d for d in at.get("download_button") if d.label == "Download editable Word draft")
    assert not resume_download.proto.disabled


def test_app_interview_prompt_uses_last_job(app_env, pg_url):
    app_env.setenv("DATABASE_URL", pg_url)
    from careerpilot.storage import Storage
    store = Storage(pg_url)
    store.init_schema()
    store.save("resume", SAMPLE_RESUME)
    store.save("last_job", "Program Manager\n" + SAMPLE_JD)
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label == "Build interview prompt").click()
    at.run()
    assert not at.exception and not at.error
    assert SAMPLE_JD in at.session_state["interview_prompt"]


def test_openai_mode_without_key_reports_error_but_keeps_prompt(app_env, pg_url):
    app_env.setenv("DATABASE_URL", pg_url)
    app_env.setenv("AI_PROVIDER", "openai")
    from careerpilot.storage import Storage
    store = Storage(pg_url)
    store.init_schema()
    store.save("resume", SAMPLE_RESUME)
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(t for t in at.text_area if t.label == "Paste the job description").input(SAMPLE_JD)
    next(b for b in at.button if b.label == "Generate evidence-grounded resume").click()
    at.run()
    assert any("OPENAI_API_KEY is not configured" in e.value for e in at.error)
    assert SAMPLE_JD in at.session_state["resume_prompt"]


def test_setting_diagnostics_reports_source_without_value():
    secret_value = "postgresql://u:topsecret@h/db"
    diag = setting_diagnostics("DATABASE_URL", secrets_loader=lambda: ({"DATABASE_URL": secret_value}, "ok"), environ={})
    assert diag == {"setting": "DATABASE_URL", "source": "secrets", "secrets_file": "ok",
                    "found_in_nested_section": False}
    nested = setting_diagnostics("DATABASE_URL", secrets_loader=lambda: ({"db": {"DATABASE_URL": secret_value}}, "ok"),
                                 environ={})
    assert nested["source"] == "missing" and nested["found_in_nested_section"]
    env = setting_diagnostics("DATABASE_URL", secrets_loader=lambda: (None, "missing"), environ={"DATABASE_URL": "x"})
    assert env["source"] == "environment" and env["secrets_file"] == "missing"
    assert "topsecret" not in repr(diag) + repr(nested)


def test_streamlit_secrets_file_is_read(tmp_path, monkeypatch):
    """The real st.secrets path: a root-level DATABASE_URL in secrets.toml is picked up."""
    import streamlit as st
    from careerpilot import config

    secrets_file = tmp_path / "secrets.toml"
    secrets_file.write_text('DATABASE_URL = "postgresql://u:p@h:5432/d"\nAI_PROVIDER = "manual"\n')
    monkeypatch.setattr(st.secrets, "_secrets", None)
    from streamlit import config as st_config
    monkeypatch.setattr(st_config, "get_option",
                        lambda key, _orig=st_config.get_option: [str(secrets_file)] if key == "secrets.files" else _orig(key))
    assert config.get_setting("DATABASE_URL", environ={}) == "postgresql://u:p@h:5432/d"
    assert config.setting_diagnostics("DATABASE_URL", environ={})["source"] == "secrets"

    secrets_file.write_text("DATABASE_URL = postgresql://unquoted\n")  # invalid TOML
    monkeypatch.setattr(st.secrets, "_secrets", None)
    assert config.setting_diagnostics("DATABASE_URL", environ={})["secrets_file"] == "invalid"
    monkeypatch.setattr(st.secrets, "_secrets", None)
