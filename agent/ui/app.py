"""Chainlit chat UI for the text-to-SQL agent.

Locally:   CHAINLIT_APP_ROOT=agent/ui uv run chainlit run agent/ui/app.py
           (pick Local DuckDB or Dev BigQuery at the top of the chat)
Cloud Run: the image runs this fixed to prod, as sa-agent-prod.

While the agent works, each SQL attempt appears as a live step with its query
and outcome. The answer shows the explanation (numbers filled in from the
result), an interactive chart when one fits, the result table, and the cost.
"""

from __future__ import annotations

import logging
import threading

import chainlit as cl
import polars as pl

from agent.chart import build_chart, chart_problems
from agent.config import Target, running_on_cloud_run
from agent.llm import AgentAnswer, ProgressEvent
from agent.runtime import Runtime, build_runtime

logger = logging.getLogger(__name__)

HISTORY_TURNS = 5
PROFILES = (
    {"Prod BigQuery": Target.PROD}
    if running_on_cloud_run()
    else {"Local DuckDB": Target.LOCAL, "Dev BigQuery": Target.DEV}
)
PROFILE_NOTES = {
    Target.LOCAL: "The gold tables in the local DuckDB file. Free and offline.",
    Target.DEV: "The dev_gold dataset in BigQuery, queried as sa-agent-dev.",
    Target.PROD: "The gold dataset in BigQuery, queried read-only as sa-agent-prod.",
}
STARTERS = (
    ("Revenue and orders", "What is total revenue, and how many orders is that?"),
    ("Monthly revenue chart", "Show monthly revenue in 2017 as a chart."),
    ("Late deliveries", "Which 5 customer states have the highest late delivery rate?"),
    (
        "Top states over time",
        "Chart monthly revenue in 2018 for the top 3 customer states, one line per state.",
    ),
    ("Reviews by category", "Which product categories have the best average review score?"),
)

_runtimes: dict[Target, Runtime] = {}
_runtimes_lock = threading.Lock()


def runtime_for(target: Target) -> Runtime:
    """Build the agent once per target, shared by every chat session.

    Args:
        target: Warehouse to query.

    Returns:
        The runtime.
    """
    with _runtimes_lock:
        if target not in _runtimes:
            _runtimes[target] = build_runtime(target)
        return _runtimes[target]


def current_target() -> Target:
    """The warehouse of this chat, from its profile.

    Returns:
        The target of the selected profile, or the first one.
    """
    profile = cl.user_session.get("chat_profile") or ""
    return PROFILES.get(profile, next(iter(PROFILES.values())))


@cl.set_chat_profiles
async def chat_profiles(current_user: cl.User | None = None) -> list[cl.ChatProfile]:
    """Offer one profile per warehouse this process may query.

    Args:
        current_user: Unused; the app has no login (Cloud Run IAM guards it).

    Returns:
        The chat profiles.
    """
    return [
        cl.ChatProfile(name=name, markdown_description=PROFILE_NOTES[target], default=i == 0)
        for i, (name, target) in enumerate(PROFILES.items())
    ]


@cl.set_starters
async def starters(current_user: cl.User | None = None, language: str | None = None) -> list:
    """Suggest questions on an empty chat.

    Args:
        current_user: Unused.
        language: Unused.

    Returns:
        The starter questions.
    """
    return [cl.Starter(label=label, message=message) for label, message in STARTERS]


@cl.on_chat_start
async def start() -> None:
    """Start a conversation with an empty history."""
    cl.user_session.set("history", [])


def _bytes(count: float) -> str:
    for unit in ("B", "KB", "MB"):
        if count < 1024:
            return f"{count:,.0f} {unit}" if unit == "B" else f"{count:,.1f} {unit}"
        count /= 1024
    return f"{count:,.1f} GB"


class LiveSteps:
    """Turn the agent's progress events into Chainlit steps, from its worker thread."""

    def __init__(self) -> None:
        """Start with no open step."""
        self.step: cl.Step | None = None
        self.count = 0

    def __call__(self, event: ProgressEvent) -> None:
        """Open, close, or add a step for one event.

        Args:
            event: What the agent just did.
        """
        if event.kind == "query_started":
            self.count += 1
            self.step = cl.Step(name=f"Query {self.count}", type="tool", language="sql")
            self.step.input = event.sql or ""
            cl.run_sync(self.step.send())
        elif event.kind == "query_finished" and self.step is not None:
            attempt = event.attempt
            if attempt and attempt.error:
                self.step.output = f"Refused or failed: {attempt.error}"
                self.step.is_error = True
            elif attempt:
                billed = f" · {_bytes(attempt.bytes_billed)} billed" if attempt.bytes_billed else ""
                self.step.output = f"{attempt.rows:,} rows{billed}"
            self.step.language = None
            cl.run_sync(self.step.update())
            self.step = None
        elif event.kind == "answer_rejected":
            step = cl.Step(name="Checking the numbers", type="tool")
            step.output = "Sent back: every number must come from the query result."
            cl.run_sync(step.send())


def answer_message(answer: AgentAnswer) -> cl.Message:
    """Build the reply: explanation, chart, table, and a cost line.

    Args:
        answer: The agent's answer.

    Returns:
        The message, not yet sent.
    """
    elements: list[cl.Element] = []
    result = answer.result
    if answer.status == "answered" and result is not None:
        if answer.chart and not chart_problems(answer.chart, result):
            elements.append(
                cl.Plotly(name="chart", figure=build_chart(answer.chart, result), display="inline")
            )
        table = pl.DataFrame(
            [list(row) for row in result.rows], schema=list(result.columns), orient="row"
        )
        elements.append(cl.Dataframe(name="result", data=table, display="inline"))

    cost = f"${answer.cost_usd:.4f}" if answer.cost_usd is not None else "n/a"
    footer = (
        f"{len(answer.attempts)} quer{'y' if len(answer.attempts) == 1 else 'ies'} · "
        f"{_bytes(answer.bytes_billed)} billed · LLM {cost} · {answer.latency_ms / 1000:.1f} s"
    )
    prefix = {"failed": "**Could not answer.** ", "refused": ""}.get(answer.status, "")
    return cl.Message(content=f"{prefix}{answer.explanation}\n\n*{footer}*", elements=elements)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Answer a question, showing each SQL attempt live.

    Args:
        message: The user's question.
    """
    target = current_target()
    history: list[tuple[str, str]] = cl.user_session.get("history") or []
    runtime = await cl.make_async(runtime_for)(target)
    answer = await cl.make_async(runtime.ask)(
        message.content,
        history=history[-HISTORY_TURNS:],
        session_id=cl.user_session.get("id"),
        progress=LiveSteps(),
    )
    history.append((message.content, answer.explanation))
    cl.user_session.set("history", history)
    await answer_message(answer).send()
