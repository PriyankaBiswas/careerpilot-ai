"""End-to-end Streamlit app tests in OpenAI mode with a fake API (synthetic data, PostgreSQL required)."""
import json
import os
import types

import httpx
import openai
import pytest
from streamlit.testing.v1 import AppTest

from careerpilot import library
from careerpilot.storage import Storage
from conftest import SYN_JD, SYN_RECORDS, SYN_RESUME, synthetic_draft, synthetic_pack

APP = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")


class FakeOpenAI:
    calls = []
    error = None

    def __init__(self, **kwargs):
        assert kwargs["max_retries"] == 0
        self.responses = types.SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        FakeOpenAI.calls.append(kwargs)
        if FakeOpenAI.error:
            raise FakeOpenAI.error
        name = kwargs["text"]["format"]["name"]
        payload = {
            "ResumeDraft": synthetic_draft,
            "InterviewPack": synthetic_pack,
            "EvidenceCheck": lambda: {"verdicts": [
                {"claim_id": "X1.B2", "verdict": "unsupported", "reason": "synthetic self-check flag"}]},
            "SummarySection": lambda: {"summary": [
                {"text": "Program manager focused on AWS migrations.", "evidence_ids": ["E1"],
                 "evidence_quotes": ["migration of 3 internal tools to AWS"], "support": "verified",
                 "ownership": "individual"}]},
            "JobMatch": lambda: {"requirements": [], "ranked_evidence_ids": ["E2", "E1"]},
        }[name]()
        usage = types.SimpleNamespace(input_tokens=5000, output_tokens=3000,
                                      input_tokens_details=types.SimpleNamespace(cached_tokens=0),
                                      output_tokens_details=types.SimpleNamespace(reasoning_tokens=1000))
        return types.SimpleNamespace(output_text=json.dumps(payload), status="completed", output=[],
                                     incomplete_details=None, usage=usage)


@pytest.fixture
def api_app(monkeypatch, pg_url):
    for name in ("APP_PASSWORD", "AI_PROVIDER", "OPENAI_MAX_COST_PER_OPERATION", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    FakeOpenAI.calls, FakeOpenAI.error = [], None
    store = Storage(pg_url)
    store.init_schema()
    library.add_resume(store, "PM resume", SYN_RESUME)
    library.save_evidence(store, SYN_RECORDS)
    return store


def button(at, label):
    return next(b for b in at.button if b.label == label)


def start(at_jd=SYN_JD):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    next(t for t in at.text_area if t.label == "Paste the job description").input(at_jd)
    next(t for t in at.text_input if t.key == "rs_job_title").input("Program Manager, Acme")
    return at.run()


def test_no_api_call_until_the_user_clicks(api_app):
    at = start()
    assert FakeOpenAI.calls == []
    assert any("Estimate before calling: gpt-6.1-terra" in c.value for c in at.caption)


def test_resume_generation_review_regeneration_and_versions(api_app):
    at = start()
    button(at, "Generate tailored resume").click()
    at.run()
    assert not at.exception, at.exception
    assert len(FakeOpenAI.calls) == 1 and FakeOpenAI.calls[0]["model"] == "gpt-6.1-terra"
    assert FakeOpenAI.calls[0]["store"] is False
    text = at.session_state["rs_text"]
    assert "reducing hosting cost 18%" in text and "40%" not in text and "Imaginary Labs" not in text
    assert any("Actual cost" in s.value for s in at.success)
    assert any("Role excluded" in e.value for e in at.error)
    assert library.load_list(api_app, "api_usage")[-1]["operation"] == "resume_draft"

    button(at, "Run AI evidence check").click()
    at.run()
    assert len(FakeOpenAI.calls) == 2 and FakeOpenAI.calls[1]["model"] == "gpt-6-luna"
    assert "Ran weekly stakeholder reviews" not in at.session_state["rs_text"]  # self-check excluded it

    button(at, "Regenerate section").click()
    at.run()
    assert len(FakeOpenAI.calls) == 3
    assert "Program manager focused on AWS migrations." in at.session_state["rs_text"]
    assert at.session_state["rs_verdicts"] == {}  # stale self-check results are cleared

    button(at, "Save version for this job").click()
    at.run()
    versions = library.load_list(api_app, "resume_versions")
    assert versions[-1]["job"] == "Program Manager, Acme" and "Imaginary Labs" not in versions[-1]["approved_text"]
    assert len(FakeOpenAI.calls) == 3  # saving never calls the API


def test_cost_guard_blocks_call(api_app, monkeypatch):
    monkeypatch.setenv("OPENAI_MAX_COST_PER_OPERATION", "0.001")
    at = start()
    assert button(at, "Generate tailored resume").proto.disabled
    assert any("exceeds your per-operation limit" in w.value for w in at.warning)
    assert FakeOpenAI.calls == []


def test_unknown_model_price_requires_confirmation(api_app, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-unpriced")
    at = start()
    assert button(at, "Generate tailored resume").proto.disabled
    next(c for c in at.checkbox if c.key == "rs_generate_unknown").check()
    at.run()
    assert not button(at, "Generate tailored resume").proto.disabled


def test_exhausted_credit_is_reported_gracefully(api_app):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    FakeOpenAI.error = openai.RateLimitError("quota", response=httpx.Response(429, request=request),
                                             body={"code": "insufficient_quota"})
    at = start()
    button(at, "Generate tailored resume").click()
    at.run()
    assert not at.exception
    assert any("credit is exhausted" in e.value for e in at.error)
    assert len(FakeOpenAI.calls) == 1  # no automatic retry
    assert "rs_draft" not in at.session_state


def test_interview_coach_generates_star_answers_with_flags(api_app):
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(t for t in at.text_area if t.key == "ic_query_job").input(SYN_JD)
    at.run()
    selected = next(m for m in at.multiselect if m.key.startswith("ic_selected_"))
    assert "E1" in selected.value
    button(at, "Generate STAR answers").click()
    at.run()
    assert not at.exception, at.exception
    assert len(FakeOpenAI.calls) == 1
    sent = FakeOpenAI.calls[0]["input"]
    assert "Unconfirmed award" not in sent  # unverified evidence is never sent
    text = at.session_state["ic_text"]
    assert "60-90 second spoken version" in text and "NEEDS REVIEW BEFORE USE" in text
    assert any("75" in w.value for w in at.warning)
