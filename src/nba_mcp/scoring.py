"""Pure, unmodified Hollinger Game Score; plus-minus is context only.

Formula reference: https://www.basketball-reference.com/about/glossary.html
"""

from collections.abc import Mapping


FORMULA_VERSION = "hollinger-game-score-v1"
FORMULA_SOURCE = "https://www.basketball-reference.com/about/glossary.html"
REQUIRED_FIELDS = (
    "points", "field_goals_made", "field_goals_attempted",
    "free_throws_made", "free_throws_attempted", "offensive_rebounds",
    "defensive_rebounds", "steals", "assists", "blocks", "personal_fouls", "turnovers",
)


class ScoringValidationError(ValueError):
    """Required normalized statistics are absent, invalid, or inconsistent."""


def _count(statistics, field, *, signed=False):
    """Return one normalized count or raise ScoringValidationError.

    Counts are nonnegative by default; signed=True permits negative plus-minus.
    Missing values, booleans, nulls, and non-integer/out-of-range inputs fail.
    The input mapping is only read and is never modified.
    """
    if field not in statistics:
        raise ScoringValidationError(f"Missing required statistic: {field}")
    value = statistics[field]
    if type(value) is not int or not -(2**63) <= value < 2**63 or (not signed and value < 0):
        raise ScoringValidationError(f"{field}: expected a {'signed' if signed else 'nonnegative'} 64-bit integer")
    return value


def calculate_game_score(statistics):
    """Return original Hollinger Game Score as a float from normalized counts.

    Accept a mapping containing all REQUIRED_FIELDS; additional fields are
    ignored, including plus_minus. Missing, boolean, string, fractional, negative,
    or out-of-range counts and made>attempted shots raise ScoringValidationError.
    Negative scores are valid. Integer tenths preserve exact formula arithmetic
    until the final float conversion; display with one decimal place. Inputs are
    never mutated. No network, database, rounding parameter, or adjustable weight.
    """
    if not isinstance(statistics, Mapping):
        raise ScoringValidationError("statistics must be a mapping of normalized counts")
    counts = {field: _count(statistics, field) for field in REQUIRED_FIELDS}
    for made, attempted in (("field_goals_made", "field_goals_attempted"),
                            ("free_throws_made", "free_throws_attempted")):
        if counts[made] > counts[attempted]:
            raise ScoringValidationError(f"{made} cannot exceed {attempted}")
    # Multiply every original coefficient by ten to use integer arithmetic;
    # divide once below to recover Game Score without intermediate rounding.
    tenths = (
        10 * counts["points"] + 4 * counts["field_goals_made"]
        - 7 * counts["field_goals_attempted"]
        - 4 * (counts["free_throws_attempted"] - counts["free_throws_made"])
        + 7 * counts["offensive_rebounds"] + 3 * counts["defensive_rebounds"]
        + 10 * counts["steals"] + 7 * counts["assists"] + 7 * counts["blocks"]
        - 4 * counts["personal_fouls"] - 10 * counts["turnovers"]
    )
    return tenths / 10


def evaluate_game_performance(statistics):
    """Return game_score, separate plus_minus, formula_version, and formula_source.

    Accept the same normalized mapping as calculate_game_score, additionally
    requiring signed 64-bit integer plus_minus. Raise ScoringValidationError for
    invalid inputs; no missing value becomes zero. No identifiers or coverage
    reports are copied: callers retain the original database query envelope.
    No input mutation, scoring adjustment, network, or storage side effects.
    """
    score = calculate_game_score(statistics)
    plus_minus = _count(statistics, "plus_minus", signed=True)
    return {
        "game_score": score, "plus_minus": plus_minus,
        "formula_version": FORMULA_VERSION, "formula_source": FORMULA_SOURCE,
    }
