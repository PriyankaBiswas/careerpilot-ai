import pytest

from careerpilot.pricing import (LONG_CONTEXT_THRESHOLD, actual_cost, estimate_cost, estimate_tokens, guard,
                                 load_prices)

PRICES = load_prices()


def test_default_prices_match_published_rates():
    assert PRICES["gpt-6.1-terra"]["input"] == 1.00 and PRICES["gpt-6.1-terra"]["output"] == 5.00
    assert PRICES["gpt-6-luna"]["input"] == 0.10 and PRICES["gpt-6-luna"]["output"] == 0.50


def test_estimate_is_worst_case():
    text = "x" * 35_000  # 10,000 tokens at 3.5 chars/token
    estimate = estimate_cost(PRICES, "gpt-6.1-terra", text, 16_000)
    assert estimate.input_tokens == 10_000
    assert estimate.max_cost == pytest.approx((10_000 * 1.0 + 16_000 * 5.0) / 1e6)
    assert "up to $0.0900" in estimate.label


def test_actual_cost_uses_cached_and_output_rates():
    cost = actual_cost(PRICES, "gpt-6.1-terra", input_tokens=10_000, cached_tokens=4_000, output_tokens=2_000)
    assert cost == pytest.approx((6_000 * 1.0 + 4_000 * 0.05 + 2_000 * 5.0) / 1e6)
    assert actual_cost(PRICES, "gpt-6-luna", 100_000, 0, 0) == pytest.approx(0.01)


def test_long_context_rates_apply_above_threshold():
    tokens = LONG_CONTEXT_THRESHOLD + 1
    assert actual_cost(PRICES, "gpt-6.1-terra", tokens, 0, 1_000_000) == pytest.approx(tokens * 2.0 / 1e6 + 7.5)


def test_unknown_model_has_no_price_and_guard_blocks_unless_confirmed():
    estimate = estimate_cost(PRICES, "mystery-model", "hello", 1000)
    assert estimate.max_cost is None and "cost unknown" in estimate.label
    assert guard(estimate, 1.0)
    assert guard(estimate, 1.0, allow_unknown=True) == ""
    assert actual_cost(PRICES, "mystery-model", 10, 0, 10) is None


def test_guard_blocks_over_limit():
    estimate = estimate_cost(PRICES, "gpt-6.1-terra", "x" * 3500, 16_000)
    assert guard(estimate, 1.00) == ""
    assert "exceeds" in guard(estimate, 0.01)


def test_price_overrides():
    prices = load_prices('{"gpt-6.1-terra": {"output": 6}, "custom": {"input": 1, "output": 2}}')
    assert prices["gpt-6.1-terra"]["output"] == 6.0 and prices["gpt-6.1-terra"]["input"] == 1.0
    assert actual_cost(prices, "custom", 1_000_000, 0, 1_000_000) == pytest.approx(3.0)
    with pytest.raises(ValueError):
        load_prices("{not json")


def test_estimate_tokens_rounds_up():
    assert estimate_tokens("abc") == 1 and estimate_tokens("", None) == 0
