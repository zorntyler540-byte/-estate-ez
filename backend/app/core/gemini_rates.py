"""Paid Gemini token rates (USD per 1,000,000 tokens).

Update this table when Google changes pricing. The comparison script reads it;
admin analytics still use ``GEMINI_FLASH_*_COST_PER_MILLION`` and
``GEMINI_PRO_*_COST_PER_MILLION`` in ``app.config`` (today's rates).

Current Flash Standard prices below match the analytics defaults. On
2027-01-01, paid 3.8 / 3.6 Flash Standard input goes from $0.75 to $1.50 per
million tokens and output from $3.75 to $7.50. Pro is unchanged in that
notice. These are the short-prompt rates already used for Estate-EZ analytics
(Pro prompts at or under 200k tokens). Add a row before comparing a model
that is not listed — unknown models are reported as unpriced, not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

CURRENT = "current"
JAN_2027 = "2027-01-01"


@dataclass(frozen=True)
class TokenRates:
    input_per_million: float
    output_per_million: float


# schedule -> model id -> rates
RATES: dict[str, dict[str, TokenRates]] = {
    CURRENT: {
        "gemini-2.5-pro": TokenRates(1.25, 10.00),
        "gemini-3.8-flash": TokenRates(0.75, 3.75),
        "gemini-3.6-flash": TokenRates(0.75, 3.75),
    },
    JAN_2027: {
        "gemini-2.5-pro": TokenRates(1.25, 10.00),
        "gemini-3.8-flash": TokenRates(1.50, 7.50),
        "gemini-3.6-flash": TokenRates(1.50, 7.50),
    },
}


def rates_for(model: str, schedule: str) -> TokenRates | None:
    return RATES.get(schedule, {}).get((model or "").strip())


def estimate_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    *,
    schedule: str,
) -> float | None:
    """Dollar cost for one call. ``None`` when tokens or a rate row is missing."""
    if input_tokens is None or output_tokens is None:
        return None
    rates = rates_for(model, schedule)
    if rates is None:
        return None
    return (
        input_tokens * rates.input_per_million + output_tokens * rates.output_per_million
    ) / 1_000_000
