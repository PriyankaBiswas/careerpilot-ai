"""One structured OpenAI Responses API call per user action.

- Exactly one paid request per call: SDK automatic retries are disabled (max_retries=0).
- Responses are not stored server-side (store=False).
- Logs contain the operation, model, token counts and error class only, never prompts or outputs.
"""
import logging
import time
from dataclasses import dataclass

from pydantic import ValidationError

from .pricing import actual_cost

logger = logging.getLogger("careerpilot.llm")
REQUEST_TIMEOUT_SECONDS = 180


@dataclass
class Usage:
    operation: str
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost: float | None
    seconds: float

    @property
    def label(self):
        cost = f"${self.cost:.4f}" if self.cost is not None else "cost unknown"
        return (f"{self.model}: {self.input_tokens:,} input ({self.cached_tokens:,} cached) + "
                f"{self.output_tokens:,} output ({self.reasoning_tokens:,} reasoning) tokens = {cost}")

    def as_record(self):
        return {"operation": self.operation, "model": self.model, "input_tokens": self.input_tokens,
                "cached_tokens": self.cached_tokens, "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens, "cost_usd": self.cost}


class LLMError(Exception):
    """API failure with a message safe to show; `usage` is set when the failed call was still billed."""

    def __init__(self, category, message, usage=None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.usage = usage


def _usage_from(response, operation, model, prices, seconds):
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    return Usage(operation, model, input_tokens, cached, output_tokens, reasoning,
                 actual_cost(prices, model, input_tokens, cached, output_tokens), round(seconds, 1))


def classify_api_error(exc, model):
    """Map OpenAI SDK exceptions to (category, user message) without echoing the raw error body."""
    import openai

    status = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None) or ""
    if isinstance(exc, openai.RateLimitError) and code == "insufficient_quota":
        return "quota", ("Your OpenAI API credit is exhausted or your billing limit was reached. Add credit at "
                         "platform.openai.com > Billing, then try again. No resume content was lost.")
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit", "OpenAI rate limit reached. Wait a minute and click the button again."
    if isinstance(exc, openai.AuthenticationError):
        return "auth", "OPENAI_API_KEY was rejected. Check the key in Streamlit secrets."
    if isinstance(exc, (openai.NotFoundError, openai.PermissionDeniedError)) or code == "model_not_found":
        return "model_access", (f"Your API key cannot use model '{model}'. Check model access in the OpenAI "
                                "dashboard or set OPENAI_MODEL / OPENAI_FAST_MODEL to a model you can use.")
    if isinstance(exc, openai.APITimeoutError):
        return "timeout", "The OpenAI request timed out. Try again, or lower max output tokens."
    if isinstance(exc, openai.APIConnectionError):
        return "connection", "Could not reach the OpenAI API. Check your connection and try again."
    if isinstance(exc, openai.BadRequestError):
        return "bad_request", ("OpenAI rejected the request (for example an unsupported parameter for this model "
                               "or input that is too long).")
    if isinstance(exc, openai.InternalServerError) or (status and status >= 500):
        return "server", "OpenAI had a server error. Try again shortly."
    return "unknown", "The OpenAI request failed unexpectedly."


def json_schema_format(schema):
    """Strict structured-output format built from a Pydantic model (all fields required, no extras)."""
    return {"type": "json_schema", "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}


class OpenAIRunner:
    def __init__(self, api_key, prices, client=None):
        if not api_key:
            raise LLMError("config", "OPENAI_API_KEY is not configured. Add it to Streamlit secrets.")
        self.prices = prices
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
        self._client = client

    def run(self, operation, model, instructions, input_text, schema, max_output_tokens, reasoning_effort=None):
        """Make exactly one request and return (validated pydantic object, Usage)."""
        kwargs = {"model": model, "instructions": instructions, "input": input_text,
                  "text": {"format": json_schema_format(schema)},
                  "max_output_tokens": max_output_tokens, "store": False}
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        started = time.monotonic()
        try:
            response = self._client.responses.create(**kwargs)
        except Exception as exc:
            category, message = classify_api_error(exc, model)
            logger.error("llm_error operation=%s model=%s category=%s exc_type=%s status=%s",
                         operation, model, category, type(exc).__name__, getattr(exc, "status_code", None))
            raise LLMError(category, message) from None
        usage = _usage_from(response, operation, model, self.prices, time.monotonic() - started)
        if usage:
            logger.info("llm_call operation=%s model=%s input_tokens=%s cached_tokens=%s output_tokens=%s "
                        "reasoning_tokens=%s cost_usd=%s seconds=%s", operation, model, usage.input_tokens,
                        usage.cached_tokens, usage.output_tokens, usage.reasoning_tokens,
                        f"{usage.cost:.6f}" if usage.cost is not None else "unknown", usage.seconds)

        status = getattr(response, "status", "completed")
        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "max_output_tokens":
                raise LLMError("max_tokens", "The response hit the max output tokens limit before finishing "
                               "(reasoning tokens count toward it). Raise the limit in the sidebar and retry.",
                               usage)
            raise LLMError("incomplete", "The model stopped before finishing (content filter or other reason).",
                           usage)
        for item in getattr(response, "output", None) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", "") == "refusal":
                    raise LLMError("refusal", "The model declined this request.", usage)
        try:
            parsed = schema.model_validate_json(getattr(response, "output_text", "") or "")
        except (ValidationError, ValueError):
            logger.error("llm_error operation=%s model=%s category=invalid_output", operation, model)
            raise LLMError("invalid_output", "The model returned output that did not match the expected "
                           "structure. You can retry.", usage) from None
        return parsed, usage
