"""Check a chart spec against its query result, and build the interactive chart.

The model only picks the chart type and which result columns go where. The
values always come from the query result, so a valid spec cannot show
numbers the warehouse did not return.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.warehouse import QueryResult

if TYPE_CHECKING:
    import plotly.graph_objects as go

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


# Teal for one series; a colorblind-safe palette when there are several.
SINGLE_COLOR = "#0f9488"
SERIES_COLORS = ["#0f9488", "#2563eb", "#d97706", "#db2777", "#7c3aed", "#65a30d", "#0891b2"]


def _hover_format(values: list[Any]) -> str:
    """Integers without decimals, other numbers with 2."""
    numbers = [v for v in values if _is_number(v)]
    return ",.0f" if numbers and all(float(v).is_integer() for v in numbers) else ",.2f"


def build_chart(spec: dict[str, Any], result: QueryResult) -> go.Figure:
    """Build an interactive Plotly chart from a valid spec and the rows it plots.

    Every chart has tooltips, zoom and pan (drag to zoom, double-click to
    reset), and a clickable legend when there is a series (click hides one,
    double-click isolates one). Line charts use a unified hover that shows
    every series at the nearest x.

    Args:
        spec: A chart spec with no `chart_problems`.
        result: The query result to plot. The chart uses only its rows.

    Returns:
        A Plotly figure, drawn in the browser.
    """
    import plotly.graph_objects as go

    kind, x, y, series = spec["type"], spec["x"], spec["y"], spec.get("series")
    xi, yi = result.columns.index(x), result.columns.index(y)
    groups: dict[Any, list[tuple[Any, Any]]] = {}
    for row in result.rows:
        key = row[result.columns.index(series)] if series else None
        groups.setdefault(key, []).append((row[xi], row[yi]))

    y_format = _hover_format([row[yi] for row in result.rows])
    figure = go.Figure()
    for number, (key, points) in enumerate(groups.items()):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        name = str(key) if series else y
        color = SERIES_COLORS[number % len(SERIES_COLORS)] if series else SINGLE_COLOR
        hover = f"{y}: %{{y:{y_format}}}<extra>{name if series else ''}</extra>"
        if kind == "bar":
            trace = go.Bar(x=xs, y=ys, name=name, marker_color=color, hovertemplate=hover)
        elif kind == "scatter":
            trace = go.Scatter(
                x=xs,
                y=ys,
                name=name,
                mode="markers",
                marker={"color": color, "size": 9},
                hovertemplate=f"{x}: %{{x}}<br>{hover}",
            )
        else:
            trace = go.Scatter(
                x=xs,
                y=ys,
                name=name,
                mode="lines+markers",
                line={"color": color, "width": 2.5},
                marker={"size": 6},
                hovertemplate=hover,
            )
        figure.add_trace(trace)

    figure.update_layout(
        title={"text": spec.get("title") or "", "x": 0, "font": {"size": 16}},
        hovermode="x unified" if kind == "line" else "closest",
        dragmode="zoom",
        showlegend=bool(series),
        legend={
            "orientation": "h",
            "y": -0.2,
            "itemclick": "toggle",
            "itemdoubleclick": "toggleothers",
        },
        barmode="group",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif"},
        xaxis={"title": x, "showgrid": False, "type": "category" if kind != "scatter" else None},
        yaxis={"title": y, "gridcolor": "rgba(128,128,128,0.2)", "tickformat": "~s"},
    )
    return figure
