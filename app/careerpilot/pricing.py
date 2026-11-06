"""Token cost estimates, actual costs from API usage, and the per-operation cost guard.

Prices are USD per 1M tokens (OpenAI Standard tier, checked 2026-11-05 at
https://developers.openai.com/api/docs/pricing). Override or add models with the OPENAI_PRICES setting,
e.g. {"gpt-6.1-terra": {"input": 1.0, "cached_input": 0.05, "output": 5.0}}.
"""
import json
import math
from dataclasses import dataclass

LONG_CONTEXT_THRESHOLD = 272_000
CHARS_PER_TOKEN = 3.5  # conservative: overestimates tokens for English text

DEFAULT_PRICES = {
    "gpt-6.1-terra": {"input": 1.00, "cached_input": 0.05, "output": 5.00,
                      "long_input": 2.00, "long_cached_input": 0.10, "long_output": 7.50},
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50,
                   "long_input": 0.20, "long_cached_input": 0.02, "long_output": 0.75},
}


def load_prices(override_json=""):
    prices = {model: dict(rates) for model, rates in DEFAULT_PRICES.items()}
    if override_json:
        try:
            overrides = json.loads(override_json)
        except json.JSONDecodeError:
            raise ValueError("OPENAI_PRICES must be valid JSON.") from None
        for model, rates in overrides.items():
            prices.setdefault(model, {}).update({k: float(v) for k, v in rates.items()})
    return prices


def estimate_tokens(*texts):
    return math.ceil(sum(len(t or "") for t in texts) / CHARS_PER_TOKEN)


def _rates(prices, model, input_tokens):
    rates = prices.get(model)
    if not rates or not {"input", "output"} <= rates.keys():
        return None
    long = input_tokens > LONG_CONTEXT_THRESHOLD and "long_input" in rates
    return {
        "input": rates["long_input" if long else "input"],
        "cached_input": rates.get("long_cached_input" if long else "cached_input", rates["input"]),
        "output": rates.get("long_output", rates["output"]) if long else rates["output"],
    }


@dataclass
class Estimate:
    model: str
    input_tokens: int
    max_output_tokens: int
    max_cost: float | None  # None when the model has no known price

    @property
    def label(self):
        cost = f"up to ${self.max_cost:.4f}" if self.max_cost is not None else "cost unknown (no price configured)"
        return (f"{self.model}: ~{self.input_tokens:,} input tokens + up to {self.max_output_tokens:,} output "
                f"tokens, {cost}")


def estimate_cost(prices, model, input_text, max_output_tokens):
    """Worst case: estimated input at full (uncached) price plus every allowed output token."""
    input_tokens = estimate_tokens(input_text)
    rates = _rates(prices, model, input_tokens)
    if rates is None:
        return Estimate(model, input_tokens, max_output_tokens, None)
    cost = (input_tokens * rates["input"] + max_output_tokens * rates["output"]) / 1_000_000
    return Estimate(model, input_tokens, max_output_tokens, cost)


def actual_cost(prices, model, input_tokens, cached_tokens, output_tokens):
    """Cost from the API's reported usage (output tokens include reasoning tokens)."""
    rates = _rates(prices, model, input_tokens)
    if rates is None:
        return None
    uncached = max(input_tokens - cached_tokens, 0)
    return (uncached * rates["input"] + cached_tokens * rates["cached_input"]
            + output_tokens * rates["output"]) / 1_000_000


def guard(estimate, limit_usd, allow_unknown=False):
    """Return a reason the call is blocked, or '' if it may proceed."""
    if estimate.max_cost is None:
        return "" if allow_unknown else (f"No price is configured for {estimate.model}, so the cost guard cannot "
                                         "check it. Add it to OPENAI_PRICES or confirm below.")
    if estimate.max_cost > limit_usd:
        return (f"Worst-case cost ${estimate.max_cost:.4f} exceeds your per-operation limit of ${limit_usd:.2f}. "
                "Lower max output tokens, shorten the inputs, or raise the limit in the sidebar.")
    return ""
