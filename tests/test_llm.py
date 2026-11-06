"""OpenAIRunner with a fake client: one call, strict schema, usage/cost, error handling, safe logs."""
import json
import types

import httpx
import openai
import pytest

from careerpilot.llm import LLMError, OpenAIRunner, classify_api_error, json_schema_format
from careerpilot.pricing import load_prices
from careerpilot.schemas import EvidenceCheck, ResumeDraft
from conftest import synthetic_draft

PRICES = load_prices()
SECRET_CONTENT = "Jordan Example confidential resume text"


def usage(input_tokens=1000, cached=200, output=500, reasoning=300):
    return types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output,
                                 input_tokens_details=types.SimpleNamespace(cached_tokens=cached),
                                 output_tokens_details=types.SimpleNamespace(reasoning_tokens=reasoning))


def response(output_text, status="completed", reason=None, output=None, with_usage=True):
    return types.SimpleNamespace(output_text=output_text, status=status, output=output or [],
                                 incomplete_details=types.SimpleNamespace(reason=reason) if reason else None,
                                 usage=usage() if with_usage else None)


class FakeClient:
    def __init__(self, result):
        self.calls = []
        self.result = result
        self.responses = types.SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run(result, schema=ResumeDraft):
    client = FakeClient(result)
    runner = OpenAIRunner("sk-test", PRICES, client=client)
    return client, runner.run("resume_draft", "gpt-6.1-terra", "rules", SECRET_CONTENT, schema, 16_000, "medium")


def api_error(cls, status, body=None):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return cls("boom " + SECRET_CONTENT, response=httpx.Response(status, request=request), body=body)


def test_successful_call_returns_validated_model_and_cost():
    client, (parsed, used) = run(response(json.dumps(synthetic_draft())))
    assert isinstance(parsed, ResumeDraft) and parsed.experience[0].employer == "Example Corp"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["store"] is False and call["max_output_tokens"] == 16_000
    assert call["reasoning"] == {"effort": "medium"}
    assert call["text"]["format"]["strict"] is True and call["text"]["format"]["name"] == "ResumeDraft"
    assert used.cost == pytest.approx((800 * 1.0 + 200 * 0.05 + 500 * 5.0) / 1e6)
    assert used.reasoning_tokens == 300 and "$" in used.label


def test_strict_schema_requires_every_field_and_forbids_extras():
    schema = json_schema_format(ResumeDraft)["schema"]
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False


def test_missing_key_raises_config_error():
    with pytest.raises(LLMError) as info:
        OpenAIRunner("", PRICES)
    assert info.value.category == "config"


def test_default_client_disables_sdk_retries(monkeypatch):
    seen = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: seen.update(kw) or FakeClient(None))
    OpenAIRunner("sk-test", PRICES)
    assert seen["max_retries"] == 0 and seen["api_key"] == "sk-test"


def test_truncated_output_reports_billed_usage():
    with pytest.raises(LLMError) as info:
        run(response('{"contact_lines": [', status="incomplete", reason="max_output_tokens"))
    assert info.value.category == "max_tokens" and info.value.usage.cost > 0


def test_invalid_json_and_schema_mismatch_are_reported():
    for text in ("not json", json.dumps({"verdicts": [{"claim_id": "S1"}]})):
        with pytest.raises(LLMError) as info:
            run(response(text), EvidenceCheck)
        assert info.value.category == "invalid_output" and info.value.usage is not None


def test_refusal_is_reported():
    refusal = [types.SimpleNamespace(content=[types.SimpleNamespace(type="refusal")])]
    with pytest.raises(LLMError) as info:
        run(response("", output=refusal))
    assert info.value.category == "refusal"


@pytest.mark.parametrize("exc, category", [
    (api_error(openai.RateLimitError, 429, {"code": "insufficient_quota"}), "quota"),
    (api_error(openai.RateLimitError, 429, {"code": "rate_limit_exceeded"}), "rate_limit"),
    (api_error(openai.AuthenticationError, 401), "auth"),
    (api_error(openai.NotFoundError, 404, {"code": "model_not_found"}), "model_access"),
    (api_error(openai.PermissionDeniedError, 403), "model_access"),
    (api_error(openai.BadRequestError, 400), "bad_request"),
    (api_error(openai.InternalServerError, 500), "server"),
    (openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")), "timeout"),
    (openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")), "connection"),
])
def test_api_errors_are_classified_without_echoing_details(exc, category):
    with pytest.raises(LLMError) as info:
        run(exc)
    assert info.value.category == category
    assert SECRET_CONTENT not in info.value.message and "boom" not in info.value.message
    assert classify_api_error(exc, "m")[0] == category


def test_quota_message_mentions_billing():
    with pytest.raises(LLMError) as info:
        run(api_error(openai.RateLimitError, 429, {"code": "insufficient_quota"}))
    assert "credit" in info.value.message.lower()


def test_logs_contain_metadata_only(caplog):
    with caplog.at_level("INFO", logger="careerpilot"):
        run(response(json.dumps(synthetic_draft())))
        with pytest.raises(LLMError):
            run(api_error(openai.RateLimitError, 429, {"code": "insufficient_quota"}))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "llm_call operation=resume_draft" in logged and "category=quota" in logged
    for forbidden in (SECRET_CONTENT, "Example Corp", "sk-test", "boom"):
        assert forbidden not in logged
