"""Check that every number in an explanation comes from the query result.

Deterministic, no LLM: numbers are pulled out of the text and each must equal
a result cell as written (rounded to the stated precision, scaled by units
like "million", or a rate written as a percentage). Numbers that are not
claims about the data are skipped: years, dates, small integers (ranks,
months, scores), and numbers that already appear in the question.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from agent.warehouse import QueryResult

SMALL_INTEGER_MAX = 31  # Days, months, ranks, scores: rarely a claim about the data.
YEARS = range(1990, 2036)
REL_TOLERANCE = 0.005

_DATES = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_NUMBER = re.compile(
    r"(?<![\w.\-])"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"\s*(?P<unit>%|percent\b|billion\b|bn\b|million\b|mn\b|thousand\b|M\b|k\b|K\b)?"
    r"(?![\w])"
)
_SCALE = {
    "billion": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "M": 1e6,
    "thousand": 1e3,
    "k": 1e3,
    "K": 1e3,
}


@dataclass(frozen=True)
class StatedNumber:
    """A number as written in the explanation.

    Attributes:
        text: The matched text, such as "13.5 million".
        written: The number as written, before its unit (13.5).
        scale: Multiplier of the unit (1e6 for million; 1 otherwise).
        decimals: Decimal places as written.
        percent: Whether it was written as a percentage.
        trailing_zeros: Trailing zeros of a whole number without a unit
            (3 for 45,000), which mark it as rounded.
    """

    text: str
    written: float
    scale: float
    decimals: int
    percent: bool
    trailing_zeros: int

    @property
    def value(self) -> float:
        """The number with its unit applied."""
        return self.written * self.scale


def stated_numbers(text: str) -> list[StatedNumber]:
    """Pull the numbers out of a text, dates removed first.

    Args:
        text: Explanation text.

    Returns:
        Numbers in the order they appear.
    """
    numbers = []
    for match in _NUMBER.finditer(_DATES.sub(" ", text)):
        digits = match["int"].replace(",", "")
        decimals = len(match["dec"]) - 1 if match["dec"] else 0
        unit = match["unit"] or ""
        whole_without_unit = not decimals and not unit and digits.strip("0")
        numbers.append(
            StatedNumber(
                text=match.group(0).strip(),
                written=float(digits + (match["dec"] or "")),
                scale=_SCALE.get(unit, 1.0),
                decimals=decimals,
                percent=unit in ("%", "percent"),
                trailing_zeros=len(digits) - len(digits.rstrip("0")) if whole_without_unit else 0,
            )
        )
    return numbers


def _is_claim(number: StatedNumber, question_values: set[float]) -> bool:
    """Whether a stated number is a claim about the data that needs support."""
    whole = number.decimals == 0 and number.scale == 1 and not number.percent
    if whole and number.written <= SMALL_INTEGER_MAX:
        return False
    if whole and int(number.written) in YEARS and "," not in number.text:
        return False
    return number.value not in question_values


def _supports(number: StatedNumber, cell: float) -> bool:
    """Whether a result cell supports a stated number, as written."""
    candidates = [cell]
    if number.percent or abs(cell) <= 1:
        candidates.append(cell * 100)  # A rate written as a percentage.
    for value in candidates:
        if math.isclose(value, number.value, rel_tol=REL_TOLERANCE):
            return True
        # Rounded as written: "52.1%" from 52.059, "13.5 million" from 13,494,400.
        if round(value / number.scale, number.decimals) == number.written:
            return True
        # A rounded whole number: "45,000" from 45,101.
        if number.trailing_zeros and round(value, -number.trailing_zeros) == number.value:
            return True
    return False


def unsupported_numbers(
    explanation: str, results: QueryResult | list[QueryResult] | None, question: str = ""
) -> list[str]:
    """List the numbers in an explanation that no result cell supports.

    Args:
        explanation: The agent's explanation.
        results: The query results of the turn (every row, not only those
            the model saw). A number may come from any of them.
        question: The question, whose own numbers are not claims.

    Returns:
        The unsupported numbers as written, empty when every claim is backed.
    """
    question_values = {n.value for n in stated_numbers(question)}
    claims = [n for n in stated_numbers(explanation) if _is_claim(n, question_values)]
    if not claims:
        return []
    cells = [
        float(v)
        for result in ([results] if isinstance(results, QueryResult) else results or [])
        for row in result.rows
        for v in row
        if isinstance(v, int | float) and not isinstance(v, bool)
    ]
    return [n.text for n in claims if not any(_supports(n, c) for c in cells)]
