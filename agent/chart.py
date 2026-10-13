"""Check a chart spec against the query result it should plot.

The model only picks the chart type and which result columns go where. The
values always come from the query result, so a valid spec cannot show
numbers the warehouse did not return.
"""

from __future__ import annotations

from typing import Any

from agent.warehouse import QueryResult

CHART_KINDS = ("bar", "line", "scatter")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def chart_problems(spec: dict[str, Any] | None, result: QueryResult | None) -> list[str]:
    """List what is wrong with a chart spec.

    Args:
        spec: The `chart` object from the answer tool, or None.
        result: The query result the chart would plot.

    Returns:
        Problems, empty when the chart can be drawn.
    """
    if not spec:
        return ["no chart"]
    if result is None or not result.rows:
        return ["no result rows to plot"]
    problems = []
    if spec.get("type") not in CHART_KINDS:
        problems.append(f"chart type {spec.get('type')!r} is not one of {CHART_KINDS}")
    columns = set(result.columns)
    for role in ("x", "y"):
        if spec.get(role) not in columns:
            problems.append(f"{role} column {spec.get(role)!r} is not in the result")
    series = spec.get("series")
    if series is not None and series not in columns:
        problems.append(f"series column {series!r} is not in the result")
    if spec.get("y") in columns:
        index = result.columns.index(spec["y"])
        values = [row[index] for row in result.rows if row[index] is not None]
        if not values or not all(_is_number(v) for v in values):
            problems.append(f"y column {spec['y']!r} is not numeric")
    return problems
