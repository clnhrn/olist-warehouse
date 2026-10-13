"""The agent loop: Claude writes SQL, the guard checks it, the warehouse runs it.

Claude gets two tools. `run_sql` runs one query and returns the first rows or
the error, so Claude can fix its own mistakes (at most `max_attempts` queries
per question). `answer` ends the turn with an explanation, a status, and an
optional chart spec. The chart only names columns of the last result, so
every number shown comes from the warehouse, never from the model.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.config import API_KEY_SECRET, AgentSettings, Target
from agent.guard import UnsafeSqlError, guard_sql
from agent.schema import WarehouseSchema, render_schema
from agent.warehouse import QueryResult, Warehouse, WarehouseError

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
ROWS_SHOWN_TO_MODEL = 50
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million tokens: input, output, cache write (5 min), cache read.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}

CHART_TYPES = ("none", "bar", "line", "scatter")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQL query on the gold tables and get back the column "
            "names, the row count, and the first rows, or the error. Reference tables "
            "as gold.<table>."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT statement."}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "answer",
        "description": (
            "Finish the turn. Call it exactly once, after the last run_sql, or without "
            "any query when the question cannot or must not be answered from the data."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["answered", "refused"],
                    "description": (
                        "refused when the data cannot answer it or it asks to change data."
                    ),
                },
                "explanation": {
                    "type": "string",
                    "description": (
                        "1 to 3 sentences for a business reader, using numbers from "
                        "the result only."
                    ),
                },
                "chart": {
                    "type": "object",
                    "description": "How to plot the last query result. type none for no chart.",
                    "properties": {
                        "type": {"type": "string", "enum": list(CHART_TYPES)},
                        "x": {"type": ["string", "null"], "description": "Result column for x."},
                        "y": {
                            "type": ["string", "null"],
                            "description": "Numeric result column for y.",
                        },
                        "series": {
                            "type": ["string", "null"],
                            "description": "Optional result column that splits lines or bars.",
                        },
                        "title": {"type": ["string", "null"]},
                    },
                    "required": ["type", "x", "y", "series", "title"],
                    "additionalProperties": False,
                },
            },
            "required": ["status", "explanation", "chart"],
            "additionalProperties": False,
        },
    },
]

INSTRUCTIONS = """\
You answer business questions about Olist, a Brazilian e-commerce marketplace, \
from its data warehouse. Orders run from September 2016 to October 2018. Money is \
in BRL.

Query the gold star schema below with run_sql, in {dialect} SQL. Refer to tables \
as gold.<table>; nothing outside gold is readable. Each result is capped at \
{row_limit} rows and you see the first {rows_shown}, so aggregate in SQL rather \
than reading raw rows.

- When a question matches a metric below, use its definition exactly.
- Facts join dimensions on the *_key columns. Fact rows already point to the \
dim_seller version valid at purchase time; filter dim_seller on is_current only \
for a seller's current attributes.
- Dimensions have an unknown member (key '-1', or -1 for dates); leave it in \
counts unless the question excludes it.
- If a query fails, read the error and fix the query.
- Every number in your answer must appear in a query result. Compute derived \
numbers (growth, shares, differences, ratios, unit conversions) in SQL, or leave \
them out.

Finish with the answer tool. Base the explanation only on rows you received. If \
the data cannot answer the question, or the question asks to change data, call \
answer with status refused and say why. Add a chart when the user asks for one, \
or when the result is a trend over time or a comparison across many categories \
(a line for time, a bar for categories); its columns must exist in the last \
result.
"""


@dataclass
class Usage:
    """Token counts summed over every model call for one question.

    Attributes:
        input_tokens: Uncached input tokens.
        output_tokens: Output tokens, thinking included.
        cache_creation_input_tokens: Input tokens written to the cache.
        cache_read_input_tokens: Input tokens read from the cache.
        calls: Model calls made.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        """Add one response's usage.

        Args:
            usage: The `usage` object of a Messages API response.
        """
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.calls += 1

    def cost_usd(self, model: str) -> float | None:
        """Estimate the cost at list prices.

        Args:
            model: Model id the calls used.

        Returns:
            Cost in USD, or None for a model without a known price.
        """
        if model not in PRICES:
            return None
        p_in, p_out, p_write, p_read = PRICES[model]
        return (
            self.input_tokens * p_in
            + self.output_tokens * p_out
            + self.cache_creation_input_tokens * p_write
            + self.cache_read_input_tokens * p_read
        ) / 1_000_000


@dataclass(frozen=True)
class Attempt:
    """One run_sql call.

    Attributes:
        sql: SQL as Claude wrote it.
        guarded_sql: SQL as run, after the guard's rewrite. None if refused.
        error: Guard or warehouse error, if any.
        rows: Rows returned, on success.
        bytes_billed: BigQuery bytes billed.
    """

    sql: str
    guarded_sql: str | None = None
    error: str | None = None
    rows: int | None = None
    bytes_billed: int = 0


@dataclass
class AgentAnswer:
    """Outcome of one question.

    Attributes:
        query_id: Unique id, also the log row's id.
        question: The question as asked.
        status: `answered`, `refused`, or `failed`.
        explanation: Claude's answer, or the failure reason.
        result: Last successful query result.
        results: Every successful query result of the turn, in order.
        sql: SQL of that result, as run.
        chart: Chart spec from the answer tool, or None.
        attempts: Every run_sql call, in order.
        usage: Token counts.
        model: Model id.
        latency_ms: Time from question to answer.
        error: Last error, if the question failed.
    """

    query_id: str
    question: str
    status: str = "failed"
    explanation: str = ""
    result: QueryResult | None = None
    results: list[QueryResult] = field(default_factory=list)
    sql: str | None = None
    chart: dict[str, Any] | None = None
    attempts: list[Attempt] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: int = 0
    error: str | None = None

    @property
    def bytes_billed(self) -> int:
        """Bytes billed over every attempt."""
        return sum(a.bytes_billed for a in self.attempts)

    @property
    def cost_usd(self) -> float | None:
        """LLM cost at list prices."""
        return self.usage.cost_usd(self.model)


class MessagesClient(Protocol):
    """The one Messages API call the agent makes (a fake in tests)."""

    def create(self, **kwargs: Any) -> Any:
        """Send a Messages API request.

        Args:
            **kwargs: Request parameters.

        Returns:
            The response message.
        """
        ...


def load_api_key(settings: AgentSettings) -> str:
    """Get the Anthropic API key, never logging it.

    Args:
        settings: Agent settings. Secret Manager is read as the agent account
            (`sa-agent-dev` for the local and dev targets).

    Returns:
        `ANTHROPIC_API_KEY` if set, else the latest `anthropic-api-key` version.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    from google.cloud import secretmanager

    from agent.config import agent_settings
    from agent.warehouse import gcp_credentials

    gcp = settings if settings.target is not Target.LOCAL else agent_settings(Target.DEV)
    client = secretmanager.SecretManagerServiceClient(credentials=gcp_credentials(gcp))
    name = f"projects/{gcp.project_id}/secrets/{API_KEY_SECRET}/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode().strip()


class AnthropicMessages:
    """`MessagesClient` on the Anthropic API, with server-side refusal fallback."""

    def __init__(self, api_key: str) -> None:
        """Create the client.

        Args:
            api_key: Anthropic API key.
        """
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)

    def create(self, **kwargs: Any) -> Any:
        """See `MessagesClient.create`."""
        return self.client.beta.messages.create(
            betas=[FALLBACK_BETA], fallbacks="default", **kwargs
        )


def _text(content: list[Any]) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


class SqlAgent:
    """Answers questions with SQL over the gold tables."""

    def __init__(
        self,
        settings: AgentSettings,
        schema: WarehouseSchema,
        warehouse: Warehouse,
        messages: MessagesClient,
    ) -> None:
        """Create the agent.

        Args:
            settings: Agent settings.
            schema: Gold tables and metrics.
            warehouse: Where queries run.
            messages: Messages API client.
        """
        self.settings = settings
        self.schema = schema
        self.warehouse = warehouse
        self.messages = messages
        instructions = INSTRUCTIONS.format(
            dialect="BigQuery" if settings.dialect == "bigquery" else "DuckDB",
            row_limit=settings.row_limit,
            rows_shown=ROWS_SHOWN_TO_MODEL,
        )
        # Same bytes on every call, so the schema stays in the prompt cache.
        self.system = [
            {
                "type": "text",
                "text": instructions + "\n" + render_schema(schema),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _run_sql(self, sql: str, answer: AgentAnswer) -> dict[str, Any]:
        """Guard and run one query, recording the attempt.

        Returns:
            The tool result content for Claude, with `is_error` on failure.
        """
        guarded = None
        try:
            guarded = guard_sql(
                sql,
                known_tables=self.schema.table_names,
                dialect=self.settings.dialect,
                dataset=self.settings.gold_dataset,
                row_limit=self.settings.row_limit,
            )
            result = self.warehouse.run(guarded.sql)
        except (UnsafeSqlError, WarehouseError) as error:
            answer.attempts.append(
                Attempt(sql=sql, guarded_sql=guarded.sql if guarded else None, error=str(error))
            )
            answer.error = str(error)
            return {"content": f"Error: {error}", "is_error": True}

        answer.attempts.append(
            Attempt(
                sql=sql,
                guarded_sql=guarded.sql,
                rows=len(result.rows),
                bytes_billed=result.bytes_billed,
            )
        )
        answer.result, answer.sql, answer.error = result, guarded.sql, None
        answer.results.append(result)
        payload = {
            "columns": list(result.columns),
            "row_count": len(result.rows),
            "truncated": len(result.rows) >= self.settings.row_limit,
            "rows": result.records(ROWS_SHOWN_TO_MODEL),
        }
        return {"content": json.dumps(payload, default=str)}

    def _handle_tools(self, tool_uses: list[Any], answer: AgentAnswer) -> tuple[list, dict | None]:
        """Run every tool call of one response.

        Returns:
            The tool_result blocks, and the answer tool's input if it was called.
        """
        results, final = [], None
        for block in tool_uses:
            if block.name == "answer":
                final = block.input
                content: dict[str, Any] = {"content": "Recorded."}
            elif block.name != "run_sql":
                content = {"content": f"Unknown tool {block.name}.", "is_error": True}
            elif len(answer.attempts) >= self.settings.max_attempts:
                content = {
                    "content": "Query limit reached. Call answer now with what you have.",
                    "is_error": True,
                }
            else:
                content = self._run_sql(block.input["sql"], answer)
            results.append({"type": "tool_result", "tool_use_id": block.id, **content})
        return results, final

    def ask(self, question: str, history: list[tuple[str, str]] | None = None) -> AgentAnswer:
        """Answer one question.

        Args:
            question: The user's question.
            history: Earlier (question, explanation) pairs of the same
                conversation, oldest first, for follow-up questions.

        Returns:
            The answer, with every attempt and the token usage. Never raises
            for SQL or model problems; those end with status `failed`.
        """
        started = time.monotonic()
        answer = AgentAnswer(
            query_id=uuid.uuid4().hex, question=question, model=self.settings.model
        )
        messages: list[dict[str, Any]] = []
        for old_question, old_answer in history or []:
            messages += [
                {"role": "user", "content": old_question},
                {"role": "assistant", "content": old_answer or "(no answer)"},
            ]
        messages.append({"role": "user", "content": question})

        # Each attempt needs a call, plus one to answer and one spare.
        for _ in range(self.settings.max_attempts + 2):
            response = self.messages.create(
                model=self.settings.model,
                max_tokens=MAX_TOKENS,
                system=self.system,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
            )
            answer.usage.add(response.usage)
            if response.stop_reason == "refusal":
                answer.status, answer.error = "failed", "The model declined to answer."
                break
            # Append the whole content, thinking blocks included, unchanged.
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                # Answered in text without the answer tool: keep the text.
                answer.explanation = _text(response.content)
                answer.status = "answered" if answer.result and answer.explanation else "failed"
                break
            results, final = self._handle_tools(tool_uses, answer)
            if final is not None:
                answer.status = final["status"]
                answer.explanation = final["explanation"]
                chart = final.get("chart") or {}
                answer.chart = chart if chart.get("type", "none") != "none" else None
                if answer.status == "answered" and answer.result is None:
                    answer.status = "failed"
                    answer.error = answer.error or "Answered without a successful query."
                break
            messages.append({"role": "user", "content": results})
        else:
            answer.error = answer.error or "Ran out of model calls without an answer."

        if answer.status == "failed" and not answer.explanation:
            answer.explanation = answer.error or "No answer."
        answer.latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Question %s: %s after %d attempts, %d tokens out",
            answer.query_id,
            answer.status,
            len(answer.attempts),
            answer.usage.output_tokens,
        )
        return answer
