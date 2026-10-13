"""Eval runner: ask every question in agent/evals/questions.yml and score it.

For each question, the reference SQL runs on the same warehouse (through the
same guard), so expected answers follow the data. Scores:
    executed: the agent ran SQL and answered with a result.
    correct:  the result matches the reference (see `compare` in the YAML).
    faithful: every number in the explanation comes from the result rows
              (agent/faithfulness.py). Reported apart from accuracy.
    cost:     LLM tokens at list prices, plus BigQuery bytes billed.
Chart questions are scored separately and do not count toward accuracy.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from typing import Any

import yaml

from agent.chart import chart_problems
from agent.config import AGENT_DIR, LOCAL_AGENT_DIR
from agent.faithfulness import unsupported_numbers
from agent.guard import guard_sql
from agent.llm import AgentAnswer
from agent.runtime import Runtime
from agent.warehouse import QueryResult

logger = logging.getLogger(__name__)

QUESTIONS_PATH = AGENT_DIR / "evals" / "questions.yml"
REFERENCE_ROW_LIMIT = 10_000
REL_TOLERANCE = 0.005  # 0.5%: allows rounding and float noise, not wrong filters.
CENTS = 0.005  # Rounding to 2 decimals, for values of 1 or more.
SMALL_ABS_TOLERANCE = 0.0005  # Rates below 1, so 0.0677 never matches 0.07.
COMPARE_MODES = ("scalar", "rows", "top", "numbers", "refusal", "chart_only")


@dataclass(frozen=True)
class EvalQuestion:
    """One eval question.

    Attributes:
        id: Short unique id.
        category: Group for the report.
        question: Text sent to the agent.
        compare: How the result is scored; one of `COMPARE_MODES`.
        sql: Reference SQL, tables as gold.<table>. None for refusals.
        chart: Acceptable chart types; set only on chart questions.
        chart_series: Whether the chart needs a series column.
    """

    id: str
    category: str
    question: str
    compare: str
    sql: str | None = None
    chart: tuple[str, ...] = ()
    chart_series: bool = False


@dataclass
class EvalResult:
    """Score of one question.

    Attributes:
        id: Question id.
        category: Question category.
        question: Question text.
        compare: Compare mode.
        status: Agent status.
        executed: The agent answered with a query result.
        correct: The result matched (or the refusal was right).
        reason: Why it did not match, if it did not.
        chart_ok: Chart validity, for chart questions only.
        faithful: Whether every number in the explanation is backed by the
            result. None when there is no result to check against.
        unsupported: Numbers in the explanation that no result cell backs.
        answer_rejections: Answers sent back to fix their placeholders.
        attempts: SQL attempts.
        bytes_billed: BigQuery bytes billed by the agent.
        cost_usd: LLM cost.
        latency_ms: Agent latency.
        sql: Agent SQL as run.
        explanation: Agent explanation.
    """

    id: str
    category: str
    question: str
    compare: str
    status: str
    executed: bool
    correct: bool
    reason: str = ""
    chart_ok: bool | None = None
    faithful: bool | None = None
    unsupported: list[str] = field(default_factory=list)
    answer_rejections: int = 0
    attempts: int = 0
    bytes_billed: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    sql: str | None = None
    explanation: str = ""


@dataclass
class EvalReport:
    """All scores of one run.

    Attributes:
        target: Warehouse queried.
        model: Model id.
        started_at: Run start, ISO 8601 in UTC.
        results: One result per question.
    """

    target: str
    model: str
    started_at: str
    results: list[EvalResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Aggregate the scores.

        Returns:
            Counts, accuracy, and cost. Accuracy counts every non-chart
            question; chart questions report their own pass count.
        """
        scored = [r for r in self.results if r.category != "chart"]
        charts = [r for r in self.results if r.category == "chart"]
        answerable = [r for r in scored if r.compare != "refusal"]
        checked = [r for r in self.results if r.faithful is not None]
        correct = sum(r.correct for r in scored)
        return {
            "questions": len(self.results),
            "scored": len(scored),
            "correct": correct,
            "accuracy": round(correct / len(scored), 4) if scored else None,
            "executed_rate": (
                round(sum(r.executed for r in answerable) / len(answerable), 4)
                if answerable
                else None
            ),
            "refusals_correct": sum(r.correct for r in scored if r.compare == "refusal"),
            "refusals": sum(1 for r in scored if r.compare == "refusal"),
            "explanations_faithful": sum(bool(r.faithful) for r in checked),
            "explanations_checked": len(checked),
            "answers_sent_back": sum(r.answer_rejections for r in self.results),
            "charts_ok": sum(bool(r.chart_ok) for r in charts),
            "charts": len(charts),
            "cost_usd": round(sum(r.cost_usd for r in self.results), 4),
            "cost_per_question_usd": (
                round(sum(r.cost_usd for r in self.results) / len(self.results), 4)
                if self.results
                else None
            ),
            "bytes_billed": sum(r.bytes_billed for r in self.results),
            "mean_latency_ms": (
                int(sum(r.latency_ms for r in self.results) / len(self.results))
                if self.results
                else None
            ),
        }


def load_questions(path: Path = QUESTIONS_PATH) -> list[EvalQuestion]:
    """Read and check the eval questions.

    Args:
        path: The questions YAML file.

    Returns:
        Questions in file order.

    Raises:
        ValueError: On a duplicate id, an unknown compare mode, or a missing
            reference SQL.
    """
    questions = []
    for raw in yaml.safe_load(path.read_text())["questions"]:
        q = EvalQuestion(
            id=raw["id"],
            category=raw["category"],
            question=" ".join(raw["question"].split()),
            compare=raw["compare"],
            sql=raw.get("sql"),
            chart=tuple(raw.get("chart") or ()),
            chart_series=bool(raw.get("chart_series", False)),
        )
        if q.compare not in COMPARE_MODES:
            raise ValueError(f"{q.id}: unknown compare mode {q.compare}")
        if q.compare not in ("refusal", "chart_only") and not q.sql:
            raise ValueError(f"{q.id}: compare {q.compare} needs reference sql")
        questions.append(q)
    ids = [q.id for q in questions]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Duplicate question ids: {duplicates}")
    return questions


# Value matching ------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def numbers_match(expected: float, actual: float) -> bool:
    """Compare two numbers; a rate below 1 also matches as a percentage.

    Integers (counts) must match exactly. Other values match within 0.5%,
    or within rounding to 2 decimals (4 for values below 1).

    Args:
        expected: Reference value.
        actual: Agent value.

    Returns:
        True when they match.
    """
    if isinstance(expected, int):
        return float(expected) == float(actual)
    floor = CENTS if abs(expected) >= 1 else SMALL_ABS_TOLERANCE
    tolerance = max(floor, REL_TOLERANCE * abs(expected))
    if math.isclose(expected, actual, abs_tol=tolerance):
        return True
    return abs(expected) <= 1 and math.isclose(expected * 100, actual, abs_tol=tolerance * 100)


def values_match(expected: Any, actual: Any) -> bool:
    """Compare two cells: numbers within tolerance, text case-insensitively.

    Args:
        expected: Reference cell.
        actual: Agent cell.

    Returns:
        True when they match.
    """
    if expected is None or actual is None:
        return expected is None and actual is None
    if _is_number(expected) and _is_number(actual):
        return numbers_match(expected, float(actual))
    return str(expected).strip().casefold() == str(actual).strip().casefold()


def _cells(result: QueryResult) -> list[Any]:
    return [value for row in result.rows for value in row]


def _column(result: QueryResult, index: int) -> list[Any]:
    return [row[index] for row in result.rows]


def _columns_match(expected: list[Any], actual: list[Any]) -> bool:
    """Whether two columns hold the same values, ignoring order."""
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for value in expected:
        hit = next((i for i, a in enumerate(remaining) if values_match(value, a)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _rows_match(expected: QueryResult, actual: QueryResult, mapping: tuple[int, ...]) -> bool:
    """Whether rows match one-to-one under a column mapping, ignoring order."""
    remaining = [tuple(row[i] for i in mapping) for row in actual.rows]
    for row in expected.rows:
        hit = next(
            (
                i
                for i, other in enumerate(remaining)
                if all(values_match(e, a) for e, a in zip(row, other, strict=True))
            ),
            None,
        )
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def compare_result(mode: str, expected: QueryResult, actual: QueryResult | None) -> str:
    """Score an agent result against the reference.

    Args:
        mode: `scalar`, `rows`, `top`, or `numbers`.
        expected: Reference result.
        actual: Agent result.

    Returns:
        An empty string on a match, else the reason it does not match.
    """
    if actual is None or not actual.rows:
        return "no result"
    if not expected.rows:
        return "reference returned no rows"
    cells = _cells(actual)
    if mode == "scalar":
        value = expected.rows[0][0]
        return "" if any(values_match(value, c) for c in cells) else f"{value} not in result"
    if mode == "top":
        wanted = expected.rows[0]
        first = actual.rows[0]
        missing = [w for w in wanted if not any(values_match(w, c) for c in first)]
        return "" if not missing else f"first row lacks {missing}"
    if mode == "numbers":
        wanted = [c for c in _cells(expected) if _is_number(c)]
        missing = [w for w in wanted if not any(values_match(w, c) for c in cells)]
        return "" if not missing else f"{len(missing)} of {len(wanted)} numbers missing"
    if mode == "rows":
        if len(actual.rows) != len(expected.rows):
            return f"{len(actual.rows)} rows, expected {len(expected.rows)}"
        width = len(expected.columns)
        candidates = [
            [
                j
                for j in range(len(actual.columns))
                if _columns_match(_column(expected, i), _column(actual, j))
            ]
            for i in range(width)
        ]
        for i, options in enumerate(candidates):
            if not options:
                return f"no column matches {expected.columns[i]}"
        for mapping in permutations(range(len(actual.columns)), width):
            if all(mapping[i] in candidates[i] for i in range(width)) and _rows_match(
                expected, actual, mapping
            ):
                return ""
        return "columns match but rows do not"
    raise ValueError(f"Unknown compare mode {mode}")


# Running -------------------------------------------------------------------


def reference_result(runtime: Runtime, sql: str) -> QueryResult:
    """Run a reference query through the same guard and warehouse as the agent.

    Args:
        runtime: The agent runtime.
        sql: Reference SQL with gold.<table> names.

    Returns:
        The reference rows.
    """
    settings = runtime.settings
    guarded = guard_sql(
        sql,
        known_tables=runtime.agent.schema.table_names,
        dialect=settings.dialect,
        dataset=settings.gold_dataset,
        row_limit=REFERENCE_ROW_LIMIT,
    )
    return runtime.agent.warehouse.run(guarded.sql)


def score(question: EvalQuestion, answer: AgentAnswer, expected: QueryResult | None) -> EvalResult:
    """Score one answer.

    Args:
        question: The eval question.
        answer: The agent's answer.
        expected: Reference result, or None for refusal and chart-only questions.

    Returns:
        The score.
    """
    executed = answer.status == "answered" and answer.result is not None
    if question.compare == "refusal":
        correct = answer.status == "refused"
        reason = "" if correct else f"expected a refusal, got {answer.status}"
    elif question.compare == "chart_only":
        correct, reason = executed, "" if executed else f"status {answer.status}"
    elif not executed:
        correct, reason = False, answer.error or f"status {answer.status}"
    elif expected is None:
        raise ValueError(f"{question.id}: compare {question.compare} needs a reference result")
    else:
        reason = compare_result(question.compare, expected, answer.result)
        correct = not reason

    faithful, unsupported = None, []
    if executed:
        unsupported = unsupported_numbers(answer.explanation, answer.results, question.question)
        faithful = not unsupported

    chart_ok = None
    if question.chart:
        problems = chart_problems(answer.chart, answer.result)
        if answer.chart and answer.chart.get("type") not in question.chart:
            problems.append(f"chart type {answer.chart.get('type')} not in {list(question.chart)}")
        if question.chart_series and answer.chart and not answer.chart.get("series"):
            problems.append("chart needs a series column")
        chart_ok = not problems
        if problems:
            reason = "; ".join(filter(None, [reason, *problems]))

    return EvalResult(
        id=question.id,
        category=question.category,
        question=question.question,
        compare=question.compare,
        status=answer.status,
        executed=executed,
        correct=correct,
        reason=reason,
        chart_ok=chart_ok,
        faithful=faithful,
        unsupported=unsupported,
        answer_rejections=answer.answer_rejections,
        attempts=len(answer.attempts),
        bytes_billed=answer.bytes_billed,
        cost_usd=round(answer.cost_usd or 0.0, 6),
        latency_ms=answer.latency_ms,
        sql=answer.sql,
        explanation=answer.explanation,
    )


def run_eval(runtime: Runtime, questions: list[EvalQuestion]) -> EvalReport:
    """Ask and score every question, one at a time.

    Args:
        runtime: The agent runtime. Every question is also logged, under a
            session id `eval-<timestamp>`.
        questions: Questions to run.

    Returns:
        The report.
    """
    started = datetime.now(UTC)
    report = EvalReport(
        target=str(runtime.settings.target),
        model=runtime.settings.model,
        started_at=started.isoformat(),
    )
    session_id = f"eval-{started:%Y%m%dT%H%M%S}"
    for number, question in enumerate(questions, start=1):
        expected = reference_result(runtime, question.sql) if question.sql else None
        answer = runtime.ask(question.question, session_id=session_id)
        result = score(question, answer, expected)
        report.results.append(result)
        logger.info(
            "[%d/%d] %s: %s%s",
            number,
            len(questions),
            question.id,
            "pass" if result.correct else "FAIL",
            f" ({result.reason})" if result.reason else "",
        )
    return report


def render_markdown(report: EvalReport) -> str:
    """Render the report for humans.

    Args:
        report: The eval report.

    Returns:
        Markdown with the summary, a per-category table, and every failure.
    """
    s = report.summary()
    lines = [
        f"# Agent eval: {report.target}, {report.model}, {report.started_at[:19]}Z",
        "",
        f"- Accuracy: **{s['correct']}/{s['scored']} ({s['accuracy']:.0%})**",
        f"- SQL ran and answered: {s['executed_rate']:.0%} of answerable questions",
        f"- Refusals right: {s['refusals_correct']}/{s['refusals']}",
        f"- Charts valid: {s['charts_ok']}/{s['charts']}",
        f"- Explanations faithful (every number from the result): "
        f"{s['explanations_faithful']}/{s['explanations_checked']}",
        f"- Answers sent back to fix placeholders: {s['answers_sent_back']}",
        f"- LLM cost: ${s['cost_usd']:.2f} total, ${s['cost_per_question_usd']:.4f} per question",
        f"- BigQuery bytes billed: {s['bytes_billed']:,}",
        f"- Mean latency: {s['mean_latency_ms']:,} ms",
        "",
        "| Category | Correct |",
        "|---|---|",
    ]
    categories: dict[str, list[EvalResult]] = {}
    for r in report.results:
        categories.setdefault(r.category, []).append(r)
    for name, results in categories.items():
        if name == "chart":
            lines.append(
                f"| chart (valid spec) | {sum(bool(r.chart_ok) for r in results)}/{len(results)} |"
            )
        else:
            lines.append(f"| {name} | {sum(r.correct for r in results)}/{len(results)} |")
    failures = [r for r in report.results if not r.correct or r.chart_ok is False]
    unfaithful = [r for r in report.results if r.faithful is False]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        lines += [
            f"### {r.id} ({r.category}, {r.compare})",
            f"- Question: {r.question}",
            f"- Status: {r.status}; attempts: {r.attempts}",
            f"- Reason: {r.reason}",
            f"- Agent said: {r.explanation}",
            "",
            "```sql",
            r.sql or "-- no SQL",
            "```",
            "",
        ]
    lines += ["", f"## Unsupported numbers in explanations ({len(unfaithful)})", ""]
    for r in unfaithful:
        lines += [
            f"### {r.id}",
            f"- Numbers not in the result: {', '.join(r.unsupported)}",
            f"- Agent said: {r.explanation}",
            "",
        ]
    return "\n".join(lines)


def write_report(report: EvalReport, out_dir: Path = LOCAL_AGENT_DIR) -> tuple[Path, Path]:
    """Write the report as JSON and Markdown.

    Args:
        report: The eval report.
        out_dir: Output folder.

    Returns:
        Paths of the JSON and Markdown files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"eval-{report.target}-{report.started_at[:19].replace(':', '')}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    payload = {
        "target": report.target,
        "model": report.model,
        "started_at": report.started_at,
        "summary": report.summary(),
        "results": [asdict(r) for r in report.results],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return json_path, md_path
