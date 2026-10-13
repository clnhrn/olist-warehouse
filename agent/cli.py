"""Command line entry point.

python -m agent ask "question" [--target local|dev] [--json]
python -m agent eval [--target local|dev] [--ids ID ...] [--min-accuracy 0.8]

local queries DuckDB (data/warehouse.duckdb); dev queries BigQuery dev_gold as
sa-agent-dev. There is no prod target here: prod runs only in Cloud Run.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from agent.config import Target
from agent.eval import load_questions, run_eval, write_report
from agent.llm import AgentAnswer
from agent.runtime import Runtime, build_runtime

logger = logging.getLogger("agent")

LOCAL_TARGETS = [Target.LOCAL, Target.DEV]
ROWS_PRINTED = 20


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="Answer one question.")
    ask.add_argument("question")
    ask.add_argument("--target", type=Target, choices=LOCAL_TARGETS, default=Target.LOCAL)
    ask.add_argument("--json", action="store_true", help="Print the answer as JSON.")
    evaluate = sub.add_parser("eval", help="Run the eval set and write a report.")
    evaluate.add_argument("--target", type=Target, choices=LOCAL_TARGETS, default=Target.LOCAL)
    evaluate.add_argument("--ids", nargs="+", help="Run only these question ids.")
    evaluate.add_argument(
        "--min-accuracy", type=float, help="Exit 1 when accuracy is below this (0 to 1)."
    )
    return parser.parse_args(argv)


def _format_table(answer: AgentAnswer) -> str:
    if answer.result is None:
        return ""
    columns = answer.result.columns
    rows = [[str(v) for v in row] for row in answer.result.rows[:ROWS_PRINTED]]
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(columns)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))]
    lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in rows]
    if len(answer.result.rows) > ROWS_PRINTED:
        lines.append(f"... {len(answer.result.rows) - ROWS_PRINTED} more rows")
    return "\n".join(lines)


def _format_answer(answer: AgentAnswer) -> str:
    cost = answer.cost_usd
    parts = [
        f"[{answer.status}] {answer.explanation}",
        "",
        f"SQL:\n{answer.sql}" if answer.sql else "SQL: none",
        "",
        _format_table(answer),
        "",
        f"attempts={len(answer.attempts)} bytes_billed={answer.bytes_billed:,} "
        f"tokens_in={answer.usage.input_tokens + answer.usage.cache_read_input_tokens:,} "
        f"tokens_out={answer.usage.output_tokens:,} "
        f"cost_usd={cost:.4f} latency_ms={answer.latency_ms:,}"
        if cost is not None
        else f"attempts={len(answer.attempts)} latency_ms={answer.latency_ms:,}",
    ]
    return "\n".join(parts).strip() + "\n"


def _to_json(answer: AgentAnswer) -> str:
    return json.dumps(
        {
            "query_id": answer.query_id,
            "status": answer.status,
            "explanation": answer.explanation,
            "sql": answer.sql,
            "columns": list(answer.result.columns) if answer.result else None,
            "rows": answer.result.records() if answer.result else None,
            "chart": answer.chart,
            "attempts": len(answer.attempts),
            "bytes_billed": answer.bytes_billed,
            "cost_usd": answer.cost_usd,
            "latency_ms": answer.latency_ms,
            "error": answer.error,
        },
        default=str,
        indent=2,
    )


def _run_eval(runtime: Runtime, ids: list[str] | None, min_accuracy: float | None) -> int:
    questions = load_questions()
    if ids:
        unknown = sorted(set(ids) - {q.id for q in questions})
        if unknown:
            raise SystemExit(f"Unknown question ids: {unknown}")
        questions = [q for q in questions if q.id in ids]
    report = run_eval(runtime, questions)
    json_path, md_path = write_report(report)
    summary = report.summary()
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    logger.info("Report: %s and %s", json_path, md_path)
    if min_accuracy is not None and (summary["accuracy"] or 0) < min_accuracy:
        logger.error("Accuracy %.2f is below %.2f", summary["accuracy"] or 0, min_accuracy)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Arguments; `sys.argv[1:]` when None.

    Returns:
        For ask: 0 when answered or refused, 1 when the question failed.
        For eval: 0, or 1 when accuracy is below --min-accuracy.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    runtime = build_runtime(args.target)
    if args.command == "eval":
        return _run_eval(runtime, args.ids, args.min_accuracy)
    answer = runtime.ask(args.question)
    sys.stdout.write(_to_json(answer) + "\n" if args.json else _format_answer(answer))
    return 1 if answer.status == "failed" else 0
