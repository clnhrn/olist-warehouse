"""Check a chart spec against its query result, and build the interactive chart.

The model only picks the chart type and which result columns go where. The
values always come from the query result, so a valid spec cannot show
numbers the warehouse did not return.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.warehouse import QueryResult

if TYPE_CHECKING:
    import altair as alt

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


def _field_type(values: list[Any]) -> str:
    """Pick the Vega-Lite type of a column: quantitative, ordinal, or nominal."""
    present = [v for v in values if v is not None]
    if present and all(_is_number(v) for v in present):
        return "quantitative"
    # Months, dates, and timestamps as text sort correctly as ordinal values.
    if present and all(isinstance(v, str) and v[:4].isdigit() for v in present):
        return "ordinal"
    return "nominal"


def build_chart(spec: dict[str, Any], result: QueryResult) -> alt.Chart | alt.LayerChart:
    """Build an interactive chart from a valid spec and the rows it plots.

    Every chart has tooltips, zoom and pan (mouse wheel and drag, double-click
    to reset), and, with a series, a clickable legend that highlights one
    series. Line charts add a hover rule showing the values at the nearest x.

    Args:
        spec: A chart spec with no `chart_problems`.
        result: The query result to plot. The chart uses only its rows.

    Returns:
        An Altair chart, drawn in the browser by Vega-Lite.
    """
    import altair as alt
    import pandas as pd

    data = pd.DataFrame(list(result.rows), columns=list(result.columns))
    kind, x, y, series = spec["type"], spec["x"], spec["y"], spec.get("series")
    x_type = _field_type(data[x].tolist())
    if kind == "scatter" and x_type != "quantitative":
        x_type = "nominal"

    x_enc = alt.X(x, type=x_type, title=x)
    y_enc = alt.Y(y, type="quantitative", title=y)
    tooltip = [alt.Tooltip(x, type=x_type)]
    if series:
        tooltip.append(alt.Tooltip(series, type="nominal"))
    tooltip.append(alt.Tooltip(y, type="quantitative", format=",.2f"))

    # Zoom and pan: scales follow the mouse. Bars and categories zoom on y only.
    zoom_axes = ["x", "y"] if x_type == "quantitative" else ["y"]
    zoom = alt.selection_interval(bind="scales", encodings=zoom_axes, name="zoom")

    base = alt.Chart(data, title=spec.get("title") or "").encode(x=x_enc, y=y_enc)
    params: list[Any] = [zoom]
    opacity: Any = alt.value(1)
    if series:
        legend = alt.selection_point(fields=[series], bind="legend", name="legend")
        params.append(legend)
        base = base.encode(color=alt.Color(series, type="nominal"))
        opacity = alt.when(legend).then(alt.value(1)).otherwise(alt.value(0.15))

    if kind == "bar":
        offset = {"xOffset": alt.XOffset(series, type="nominal")} if series else {}
        return (
            base.mark_bar().encode(opacity=opacity, tooltip=tooltip, **offset).add_params(*params)
        )
    if kind == "scatter":
        return (
            base.mark_circle(size=70).encode(opacity=opacity, tooltip=tooltip).add_params(*params)
        )

    # Line: the hover rule follows the nearest x and shows every series there.
    hover = alt.selection_point(
        fields=[x], nearest=True, on="pointerover", empty=False, name="hover"
    )
    lines = base.mark_line(point=True).encode(opacity=opacity).add_params(*params)
    catcher = (
        base.mark_point(size=120).encode(opacity=alt.value(0), tooltip=tooltip).add_params(hover)
    )
    rule = (
        alt.Chart(data)
        .mark_rule(color="gray")
        .encode(x=x_enc, tooltip=tooltip)
        .transform_filter(hover)
    )
    points = base.mark_circle(size=70).encode(
        opacity=alt.when(hover).then(alt.value(1)).otherwise(alt.value(0))
    )
    return alt.layer(lines, catcher, rule, points)
