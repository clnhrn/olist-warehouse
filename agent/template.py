"""Fill the explanation's number placeholders from the query result.

The model never writes a number about the data. It writes a placeholder, such
as `{{r1.revenue:money}}` (row 1, column revenue, formatted as money), and
this module puts in the exact value from the result. So every number shown
equals the warehouse's value, formatted by fixed rules, never rounded by the
model.

Formats:
    int:   45,101
    num:   12.56 (2 decimals)
    money: 1,003,862.14 BRL
    pct:   6.77% (a rate from 0 to 1, times 100, 2 decimals)
Without a format, integers render as int, other numbers as num, text as is.
"""

from __future__ import annotations

import re
from typing import Any

from agent.faithfulness import is_claim, stated_numbers
from agent.warehouse import QueryResult

PLACEHOLDER = re.compile(r"\{\{\s*r(?P<row>\d+)\.(?P<column>[A-Za-z_]\w*)(?::(?P<fmt>\w+))?\s*\}\}")
FORMATS = ("int", "num", "money", "pct")


class TemplateError(ValueError):
    """The explanation cannot be rendered. The message tells the model what to fix."""


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def format_value(value: Any, fmt: str | None) -> str:
    """Format one result value.

    Args:
        value: A result cell.
        fmt: One of `FORMATS`, or None for the default.

    Returns:
        The formatted value.

    Raises:
        TemplateError: If a number format is used on a value that is not a number.
    """
    if value is None:
        return "no value"
    if fmt is None:
        if isinstance(value, bool) or not _is_number(value):
            return str(value)
        fmt = "int" if isinstance(value, int) or float(value).is_integer() else "num"
    if not _is_number(value):
        raise TemplateError(f"Format {fmt} needs a number, got {value!r}.")
    number = float(value)
    if fmt == "int":
        return f"{number:,.0f}"
    if fmt == "num":
        return f"{number:,.2f}"
    if fmt == "money":
        return f"{number:,.2f} BRL"
    if fmt == "pct":
        return f"{number * 100:,.2f}%"
    raise TemplateError(f"Unknown format {fmt}; use one of {', '.join(FORMATS)}.")


def render(template: str, result: QueryResult | None, question: str = "") -> str:
    """Replace every placeholder with its formatted value.

    Args:
        template: Explanation written with placeholders.
        result: The last query result, whose rows the placeholders name.
        question: The question; numbers it contains may be repeated as is.

    Returns:
        The explanation with exact values.

    Raises:
        TemplateError: If a placeholder names a missing row or column, a
            format does not fit the value, or a number about the data is
            written out instead of as a placeholder. All problems are listed.
    """
    problems: list[str] = []

    def fill(match: re.Match[str]) -> str:
        row, column, fmt = int(match["row"]), match["column"], match["fmt"]
        if result is None:
            problems.append(f"{match.group(0)}: there is no query result.")
            return match.group(0)
        if not 1 <= row <= len(result.rows):
            problems.append(
                f"{match.group(0)}: row {row} does not exist (1 to {len(result.rows)})."
            )
            return match.group(0)
        if column not in result.columns:
            problems.append(
                f"{match.group(0)}: no column {column}; columns are {list(result.columns)}."
            )
            return match.group(0)
        try:
            return format_value(result.rows[row - 1][result.columns.index(column)], fmt)
        except TemplateError as error:
            problems.append(f"{match.group(0)}: {error}")
            return match.group(0)

    rendered = PLACEHOLDER.sub(fill, template)

    question_values = {n.value for n in stated_numbers(question)}
    raw = [
        n.text
        for n in stated_numbers(PLACEHOLDER.sub(" ", template))
        if is_claim(n, question_values)
    ]
    if raw:
        problems.append(
            f"Numbers written out instead of as placeholders: {', '.join(raw)}. Write each as "
            "{{r<row>.<column>:<format>}}; if the value is not in the result, compute it in SQL "
            "or leave it out."
        )
    if problems:
        raise TemplateError(" ".join(problems))
    return rendered
